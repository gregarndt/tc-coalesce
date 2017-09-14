import sys
import os
import flask
import time
import redis
import logging
from flask import jsonify
from urlparse import urlparse
from werkzeug.contrib.fixers import ProxyFix
from flask_sslify import SSLify

starttime = time.time()

app = flask.Flask(__name__)

if 'DYNO' in os.environ:
    app.wsgi_app = ProxyFix(app.wsgi_app)
    SSLify(app, age=300, permanent=True)


def setup_logging(app):
    stream_handler = logging.StreamHandler(sys.stdout)
    formatter = logging.Formatter('[%(asctime)s] [%(process)d] ' +
                                  '[%(levelname)s] %(message)s',
                                  datefmt='%Y-%m-%d %H:%M:%S +0000')
    stream_handler.setFormatter(formatter)
    if os.getenv('DEBUG'):
        app.logger.setLevel(logging.DEBUG)
    else:
        app.logger.setLevel(logging.INFO)
    app.logger.addHandler(stream_handler)
    return app


def load_config(app):
    # Load setting from config.py
    # Valid environment types are Production, Testing, Development (Default)
    environment_type = os.getenv('ENVIRONMENT_TYPE', 'Development')
    app.logger.info('ENVIRONMENT_TYPE set to {0}'.format(environment_type))
    app.config.from_object('config.config.{0}'.format(environment_type))

    # Override with select environment vars if they exist
    if os.getenv('REDIS_URL'):
        app.config['REDIS_URL'] = os.getenv('REDIS_URL')
    if os.getenv('PREFIX'):
        app.config['PREFIX'] = os.getenv('PREFIX')
    if os.getenv('DEBUG'):
        app.config['DEBUG'] = os.getenv('DEBUG')
    return app


def connect_redis(app):
    redis_url = urlparse(app.config['REDIS_URL'])
    app.logger.info('Connecting to Redis @ {0}'.format(
                    app.config['REDIS_URL']))
    app.redis = redis.Redis(host=redis_url.hostname,
                            port=redis_url.port,
                            password=redis_url.password,
                            decode_responses=True)
    return app


def set_prefix(app):
    app.prefix = app.config['PREFIX']
    return app


# Setup application
app = setup_logging(app)
app = load_config(app)
app = connect_redis(app)
app = set_prefix(app)


@app.route('/')
def root():
    """
    GET: Return an index of available api
    """
    # TODO: return an index on available api
    return jsonify({'versions': ['v1']})


@app.route('/v1/ping')
def ping():
    """ GET: return web process uptime """
    ping = {'alive': True, 'uptime': time.time() - starttime}
    return jsonify(ping)


@app.route('/v1/list')
def coalasce_lists():
    """
    GET: returns a list of all coalesced objects load into the listener
    """
    list_keys_set = app.redis.smembers(app.prefix + "list_keys")
    if len(list_keys_set) == 0:
        return jsonify({app.prefix: []})
    list_keys = [x for x in list_keys_set]
    return jsonify({app.prefix: list_keys})


@app.route('/v1/stats')
def stats():
    """
    GET: returns stats
    """
    prefix_key = app.prefix + 'stats'
    stats = app.redis.hgetall(prefix_key)
    return flask.jsonify(stats)


@app.route('/v1/list/<int:age>/<int:size>/<key>')
def list(age, size, key):
    """
    GET: returns a json object with the single property 'supersedes' which
    contains either an ordered list of taskIds associated with <key>, when at
    least one of those tasks is older than <age> seconds, and there are more
    than <size> entries, otherwise if either of these criteria is not met, an
    empty list.
    """

    prefix_key = app.prefix + 'lists.' + key
    empty_resp = jsonify({'supersedes': []})
    coalesced_list = app.redis.lrange(prefix_key, 0, -1)

    # Return empty resp if list is empty
    if len(coalesced_list) == 0:
        return empty_resp

    # Return empty resp if taskid list is
    # less than or equal to the size threshold
    if len(coalesced_list) <= size:
        app.logger.debug("List does not meet size threshold")
        return empty_resp

    # Get age of oldest taskid in the list
    oldest_task_age = app.redis.get(app.prefix +
                                    coalesced_list[-1] + '.timestamp')

    # Return empty resp if age of the oldest taskid in list is
    # less than or equal to the age threshold
    if (time.time() - float(oldest_task_age)) <= age:
        app.logger.debug("Oldest task in list does not meet age threshold")
        return empty_resp

    # Thresholds have been exceeded. Return list for coalescing
    app.logger.info("{0} supersedes tasks {1}".format(coalesced_list[-1], coalesced_list[:-1]))
    return jsonify({'supersedes': coalesced_list})


def action_response(action, success=True, status_code=200):
    """ Returns a stock json response """
    resp = jsonify({'action': action, 'success': success})
    resp.status_code = status_code
    return resp


if __name__ == '__main__':
    # TODO: remove debug arg
    app.run(host='0.0.0.0', debug=False)

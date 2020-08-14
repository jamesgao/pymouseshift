import os
import json
import secrets
import appdirs
from collections import namedtuple

Event = namedtuple('Event', ['type', 'code', 'value'])

def clamp(val, lo, hi):
    return lo if val < lo else hi if val > hi else val

def save_config():
    json.dump(config, open(confpath, 'w'))

class ClientDB(object):
    def __init__(self, jspath):
        self.jspath = jspath
        try:
            db = json.load(open(jspath, "r"))
        except json.decoder.JSONDecodeError:
            db = dict()
        self.db = dict((tuple(k.split(',')), v) for k, v in db.items())

    def __contains__(self, client):
        try:
            return (client.hostname, client.token) in self.db
        except AttributeError:
            return (client['hostname'], client['token']) in self.db

    def get_client(self, hostname, token):
        desc = self.db[(hostname, token)]
        return desc

    def update_client(self, client):
        self.db[(client.hostname, client.token)] = dict(
            hostname=client.hostname,
            token=client.token,
            topleft=(client.xlim[0],client.ylim[0]),
            bottomright=(client.xlim[1],client.ylim[1]), 
            resolution=client.resolution
        ) 
        self.save()

    def save(self):
        db = dict((f'{k[0]},{k[1]}', v) for k, v in self.db.items())
        json.dump(db, open(self.jspath, "w"))

config_dir = appdirs.user_config_dir("pymouseshift")
dbpath = os.path.join(config_dir, "client_db.json")
confpath = os.path.join(config_dir, "config.json")
cert_dir = os.path.join(config_dir, 'server_certs')

if not os.path.exists(config_dir):
    os.makedirs(config_dir, mode=0o700)
if not os.path.exists(cert_dir):
    os.makedirs(cert_dir, mode=0o700)
if not os.path.exists(dbpath):
    json.dump({}, open(dbpath, "w"))

try:
    config = json.load(open(confpath))
except (FileNotFoundError, json.decoder.JSONDecodeError):
    config = dict(token=secrets.token_urlsafe(), servers=[])
    save_config()

db = ClientDB(dbpath)
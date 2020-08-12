import os
import json
import socket
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
        db = json.load(open(jspath, "r"))
        self.db = dict((tuple(k.split(',')), v) for k, v in db.items())

    def __contains__(self, client):
        try:
            return (client.hostname, client.token) in self.db
        except AttributeError:
            return (client['hostname'], client['token']) in self.db

    def get_client(self, hostname, token):
        desc = self.db[(hostname, token)]
        return desc

    def add_client(self, client):
        self.db[(client.hostname, client.token)] = dict(
            hostname=client.hostname,
            token=client.token,
            topleft=(client.xlim[0],client.ylim[0]),
            bottomright=(client.xlim[1],client.ylim[1]))
        self.save()

    def save(self):
        db = dict((f'{k[0]},{k[1]}', v) for k, v in self.db.items())
        json.dump(db, open(self.jspath, "w"))

def gen_cert(name=socket.gethostname()):
    """Generate a self-signed certificate to encrypt traffic"""
    from OpenSSL import crypto, SSL
    k = crypto.PKey()
    k.generate_key(crypto.TYPE_RSA, 4096)
    cert = crypto.X509()
    cert.get_subject().CN = name
    cert.set_serial_number(0)
    cert.gmtime_adj_notBefore(0)
    cert.gmtime_adj_notAfter(365*24*60*60*5) #5 year cert renewal
    cert.set_issuer(cert.get_subject())
    cert.set_pubkey(k)
    cert.sign(k, 'sha512')
    with open(os.path.join(config_dir, f'{name}.crt'), 'wt') as fp:
        fp.write(crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode('utf-8'))
    with open(os.path.join(config_dir, f'{name}.key'), 'wt') as fp:
        fp.write(crypto.dump_privatekey(crypto.FILETYPE_PEM, k).decode('utf-8'))

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
if not os.path.exists(confpath):
    json.dump(dict(token=secrets.token_urlsafe(), last_address=""), open(confpath, "w"))

config = json.load(open(confpath))
db = ClientDB(dbpath)
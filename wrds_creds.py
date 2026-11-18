import os

def read_pgpass():
    pgpass_path = os.path.expanduser("~/.pgpass")
    with open(pgpass_path, "r") as f:
        lines = [ln.strip() for ln in f if ln.strip() and not ln.startswith('#')]
    # .pgpass format: host:port:database:username:password (last entry wins)
    fields = lines[-1].split(':')
    uname, pword = fields[-2], fields[-1]
    return uname,pword
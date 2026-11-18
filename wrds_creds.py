import os

def read_pgpass():
    pgpass_path = os.path.expanduser("~/.pgpass")
    with open(pgpass_path, "r") as f:
        uname=f.read().split(':')[-2]
        pword=f.read().split(':')[-1]
    return uname,pword
#!/usr/bin/env python3
"""Generate a bcrypt hash for PANEL_ADMIN_PASSWORD_HASH.

Usage: pip install passlib[bcrypt]; python3 gen_password_hash.py
"""
import getpass
from passlib.hash import bcrypt

if __name__ == "__main__":
    pw = getpass.getpass("Admin password: ")
    print(bcrypt.hash(pw))

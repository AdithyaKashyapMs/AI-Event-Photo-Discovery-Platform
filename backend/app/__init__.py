"""Command 2: touch backend/app/__init__.py
touch creates an empty file if it doesn't already exist.

The filename is __init__.py — two underscores before init and two after it.
Python uses this file to mark a directory as a regular Python package. This makes it easier to organize code into modules and import them, for example:
from app import something
Python also supports namespace packages without __init__.py, but we're using the conventional package structure here.
The file may remain completely empty. You do not need to add code to it yet."""
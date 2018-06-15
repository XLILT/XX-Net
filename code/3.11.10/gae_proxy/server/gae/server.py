#!/usr/bin/env python
# coding:utf-8

import sys
import os

current_path = os.path.dirname(os.path.abspath(__file__))
root_path = os.path.abspath( os.path.join(current_path, os.pardir, os.pardir, os.pardir))
python_path = os.path.abspath( os.path.join(root_path, 'python27', '1.0'))

noarch_lib = os.path.abspath( os.path.join(python_path, 'lib', 'noarch'))
sys.path.append(noarch_lib)

if sys.platform == "win32":
    win32_lib = os.path.abspath( os.path.join(python_path, 'lib', 'win32'))
    sys.path.append(win32_lib)
elif sys.platform.startswith("linux"):
    linux_lib = os.path.abspath( os.path.join(python_path, 'lib', 'linux'))
    sys.path.append(linux_lib)
elif sys.platform == "darwin":
    darwin_lib = os.path.abspath( os.path.join(python_path, 'lib', 'darwin'))
    sys.path.append(darwin_lib)
    extra_lib = "/System/Library/Frameworks/Python.framework/Versions/2.7/Extras/lib/python"
    sys.path.append(extra_lib)

import traceback
import simple_http_server
import xlog
import openshift_handler

proxy_server = None

def main(args):
    global proxy_server

    proxy_server = simple_http_server.HTTPServer(
        ("0.0.0.0", 3003), openshift_handler.OSProxyHandler, logger=xlog)
    
    proxy_server.serve_forever()

def terminate():
    global proxy_server

    xlog.info("start to terminate GAE_Proxy")    
    proxy_server.shutdown()

if __name__ == '__main__':
    try:
        main({})
    except Exception:
        traceback.print_exc(file=sys.stdout)
    except KeyboardInterrupt:
        terminate()
        sys.exit()

#!/usr/bin/env python
# coding:utf-8

import errno
import socket
import ssl
import urlparse
import OpenSSL
NetWorkIOError = (socket.error, ssl.SSLError, OpenSSL.SSL.Error, OSError)

import zlib
import struct

from xlog import getLogger
xlog = getLogger("gae_proxy")
import simple_http_client
import simple_http_server
#from cert_util import CertUtil
#import gae_handler
#import direct_handler
#import web_control
#from front import front

def inflate(data):
    return zlib.decompress(data, -zlib.MAX_WBITS)


def deflate(data):
    return zlib.compress(data)[2:-4]

class OSProxyHandler(simple_http_server.HttpServerHandler):
    gae_support_methods = tuple(["GET", "POST", "HEAD", "PUT", "DELETE", "PATCH"])
    # GAE don't support command like OPTION

    bufsize = 65535
    local_names = []
    self_check_response_data = "HTTP/1.1 200 OK\r\n" \
                               "Access-Control-Allow-Origin: *\r\n" \
                               "Cache-Control: no-cache, no-store, must-revalidate\r\n" \
                               "Pragma: no-cache\r\n" \
                               "Expires: 0\r\n" \
                               "Content-Type: text/plain\r\n" \
                               "Keep-Alive:\r\n" \
                               "Persist:\r\n" \
                               "Connection: Keep-Alive, Persist\r\n" \
                               "Content-Length: 2\r\n\r\nOK"

    #fake_host = web_control.get_fake_host()

    def setup(self):
        self.__class__.do_GET = self.__class__.do_METHOD
        self.__class__.do_PUT = self.__class__.do_METHOD
        self.__class__.do_POST = self.__class__.do_METHOD
        self.__class__.do_HEAD = self.__class__.do_METHOD
        self.__class__.do_DELETE = self.__class__.do_METHOD
        self.__class__.do_OPTIONS = self.__class__.do_METHOD

    def forward_local(self):
        """
        If browser send localhost:xxx request to GAE_proxy,
        we forward it to localhost.
        """

        request_headers = dict((k.title(), v) for k, v in self.headers.items())
        payload = b''
        method = None
        url = None
        if 'Content-Length' in request_headers:
            try:
                payload_len = int(request_headers.get('Content-Length', 0))
                payload = self.rfile.read(payload_len)

                #print(payload)
                payload_length, = struct.unpack('!h', payload[:2])
                #print("len: ", payload_length)
                payload = inflate(payload[2:2 + payload_length])
                raw_response_line, payload = payload.split('\r\n', 1)
                method, url = raw_response_line.split()[:2]

                print(method, url)
                
            except Exception as e:
                xlog.warn('forward_local read payload failed:%s', e)
                return

        print(payload)
        #response = simple_http_client.request(self.command, self.path, request_headers, payload)
        response = simple_http_client.request(method, url, request_headers, payload)
        if not response:
            xlog.warn("forward_local fail, command:%s, path:%s, headers: %s, payload: %s",
                method, url, request_headers, payload)
            return

        out_list = []
        out_list.append("HTTP/1.1 %d\r\n" % response.status)
        for key in response.headers:
            key = key.title()
            out_list.append("%s: %s\r\n" % (key, response.headers[key]))
        out_list.append("\r\n")
        content = response.text
        if isinstance(content, memoryview):
            content = content.tobytes()
        out_list.append(content)

        #print(out_list)

        self.wfile.write("".join(out_list))

    def send_method_allows(self, headers, payload):
        xlog.debug("send method allow list for:%s %s", self.command, self.path)
        # Refer: https://developer.mozilla.org/en-US/docs/Web/HTTP/Access_control_CORS#Preflighted_requests

        response = \
                "HTTP/1.1 200 OK\r\n"\
                "Access-Control-Allow-Credentials: true\r\n"\
                "Access-Control-Allow-Methods: GET, POST, HEAD, PUT, DELETE, PATCH\r\n"\
                "Access-Control-Max-Age: 1728000\r\n"\
                "Content-Length: 0\r\n"

        req_header = headers.get("Access-Control-Request-Headers", "")
        if req_header:
            response += "Access-Control-Allow-Headers: %s\r\n" % req_header

        origin = headers.get("Origin", "")
        if origin:
            response += "Access-Control-Allow-Origin: %s\r\n" % origin
        else:
            response += "Access-Control-Allow-Origin: *\r\n"

        response += "\r\n"

        self.wfile.write(response)

    def is_local(self, hosts):
        # kkk
        return True

        if 0 == len(self.local_names):
            self.local_names.append('localhost')
            self.local_names.append(socket.gethostname().lower())
            try:
                self.local_names.append(socket.gethostbyname_ex(socket.gethostname())[-1])
            except socket.gaierror:
                # TODO Append local IP address to local_names
                pass

        for s in hosts:
            s = s.lower()
            if s.startswith('127.') \
                    or s.startswith('192.168.') \
                    or s.startswith('10.') \
                    or s.startswith('169.254.') \
                    or s in self.local_names:
                print s
                return True

        return False

    def do_CONNECT(self):
        """deploy fake cert to client"""
        host, _, port = self.path.rpartition(':')
        port = int(port)
        if port != 443:
            xlog.warn("CONNECT %s port:%d not support", host, port)
            return

        certfile = CertUtil.get_cert(host)
        self.wfile.write(b'HTTP/1.1 200 OK\r\n\r\n')

        try:
            ssl_sock = ssl.wrap_socket(self.connection, keyfile=CertUtil.cert_keyfile, certfile=certfile, server_side=True)
        except ssl.SSLError as e:
            xlog.info('ssl error: %s, create full domain cert for host:%s', e, host)
            certfile = CertUtil.get_cert(host, full_name=True)
            return
        except Exception as e:
            if e.args[0] not in (errno.ECONNABORTED, errno.ECONNRESET):
                xlog.exception('ssl.wrap_socket(self.connection=%r) failed: %s path:%s, errno:%s', self.connection, e, self.path, e.args[0])
            return

        self.__realwfile = self.wfile
        self.__realrfile = self.rfile
        self.connection = ssl_sock
        self.rfile = self.connection.makefile('rb', self.bufsize)
        self.wfile = self.connection.makefile('wb', 0)

        self.parse_request()

        self.do_METHOD()

    def do_METHOD(self):        
        return self.forward_local()

        #self.close_connection = 0
        #host = self.headers.get('Host', '')
        #host_ip, _, port = host.rpartition(':')
#
        #if self.is_local([host, host_ip]):
            #xlog.debug("Browse localhost by proxy")
            #return self.forward_local()
        #elif host == self.fake_host:
            ## xlog.debug("%s %s", self.command, self.path)
            ## for web_ui status page
            ## auto detect browser proxy setting is work
            #return self.wfile.write(self.self_check_response_data)
#
        #if isinstance(self.connection, ssl.SSLSocket):
            #method = "https"
        #else:
            #method = "http"
#
        #if self.path[0] == '/':
            #self.host = self.headers['Host']
            #self.url = '%s://%s%s' % (method,host, self.path)
        #else:
            #self.url = self.path
            #self.parsed_url = urlparse.urlparse(self.path)
            #self.host = self.parsed_url[1]
            #if len(self.parsed_url[4]):
                #self.path = '?'.join([self.parsed_url[2], self.parsed_url[4]])
            #else:
                #self.path = self.parsed_url[2]

        #if len(self.url) > 2083 and self.host.endswith(front.config.GOOGLE_ENDSWITH):
            #return self.go_DIRECT()
#
        #if self.host in front.config.HOSTS_GAE:
            #return self.go_AGENT()
#
        ## redirect http request to https request
        ## avoid key word filter when pass through GFW
        #if host in front.config.HOSTS_DIRECT:
            #if isinstance(self.connection, ssl.SSLSocket):
                #return self.go_DIRECT()
            #else:
                #xlog.debug("Host:%s Direct redirect to https", host)
                #return self.wfile.write(('HTTP/1.1 301\r\nLocation: %s\r\nContent-Length: 0\r\n\r\n' % self.path.replace('http://', 'https://', 1)).encode())
#
        #if host.endswith(front.config.HOSTS_GAE_ENDSWITH):
            #return self.go_AGENT()
#
        #if host.endswith(front.config.HOSTS_DIRECT_ENDSWITH):
            #if method == "https":
                #return self.go_DIRECT()
            #else:
                #xlog.debug("Host:%s Direct redirect to https", host)
                #return self.wfile.write(('HTTP/1.1 301\r\nLocation: %s\r\nContent-Length: 0\r\n\r\n' % self.path.replace('http://', 'https://', 1)).encode())

        #return self.go_AGENT()

    # Called by do_METHOD and do_CONNECT_AGENT
    #def go_AGENT(self):
        #def get_crlf(rfile):
            #crlf = rfile.readline(2)
            #if crlf != "\r\n":
                #xlog.warn("chunk header read fail crlf")
#
        #request_headers = dict((k.title(), v) for k, v in self.headers.items())
#
        ##for k, v in self.headers.items():
        ##    xlog.debug("%s -> %s", k, v)
#
        #payload = b''
        #if 'Content-Length' in request_headers:
            #try:
                #payload_len = int(request_headers.get('Content-Length', 0))
                ##xlog.debug("payload_len:%d %s %s", payload_len, self.command, self.path)
                #payload = self.rfile.read(payload_len)
            #except NetWorkIOError as e:
                #xlog.error('handle_method_urlfetch read payload failed:%s', e)
                #return
        #elif 'Transfer-Encoding' in request_headers:
            ## chunked, used by facebook android client
            #payload = ""
            #while True:
                #chunk_size_str = self.rfile.readline(65537)
                #chunk_size_list = chunk_size_str.split(";")
                #chunk_size = int("0x"+chunk_size_list[0], 0)
                #if len(chunk_size_list) > 1 and chunk_size_list[1] != "\r\n":
                    #xlog.warn("chunk ext: %s", chunk_size_str)
                #if chunk_size == 0:
                    #while True:
                        #line = self.rfile.readline(65537)
                        #if line == "\r\n":
                            #break
                        #else:
                            #xlog.warn("entity header:%s", line)
                    #break
                #payload += self.rfile.read(chunk_size)
                #get_crlf(self.rfile)
#
        #if self.command == "OPTIONS":
            #return self.send_method_allows(request_headers, payload)
#
        #if self.command not in self.gae_support_methods:
            #xlog.warn("Method %s not support in GAEProxy for %s", self.command, self.path)
            #return self.wfile.write(('HTTP/1.1 404 Not Found\r\n\r\n').encode())
#
        #xlog.debug("GAE %s %s from:%s", self.command, self.path, self.address_string())
        ##gae_handler.handler(self.command, self.url, request_headers, payload, self.wfile)
#
        ##kkk
        #self.go_OPENSHIFT(request_headers, payload)

    #def go_DIRECT(self):
        #xlog.debug('DIRECT %s %s', self.command, self.url)
#
        #request_headers = dict((k.title(), v) for k, v in self.headers.items())
#
        #if 'Content-Length' in request_headers:
            #try:
                #payload_len = int(request_headers.get('Content-Length', 0))
                ## xlog.debug("payload_len:%d %s %s", payload_len, self.command, self.path)
                #payload = self.rfile.read(payload_len)
            #except NetWorkIOError as e:
                #xlog.error('Direct %s read payload failed:%s', self.url, e)
                #return
        #else:
            #payload = b''
#
        #try:
            #direct_handler.handler(self.command, self.host, self.path, request_headers, payload, self.wfile)
        #except NetWorkIOError as e:
            #xlog.warn('DIRECT %s %s except:%r', self.command, self.url, e)
            #if e.args[0] not in (errno.ECONNABORTED, errno.ETIMEDOUT, errno.EPIPE):
                #raise
        #except Exception as e:
            #xlog.exception('DIRECT %s %s except:%r', self.command, self.url, e)

    #def go_OPENSHIFT(self, request_headers, payload):
        #xlog.debug("go OPENSHIFT")
        ##response = simple_http_client.request(url="http://192.168.0.49:3001/getLiveUrl")
        #response = simple_http_client.request(url="http://192.168.0.49:3001/getLiveUrl")
#
        #out_list = []
        #out_list.append("HTTP/1.1 %d\r\n" % response.status)
        #for key in response.headers:
            #key = key.title()
            #out_list.append("%s: %s\r\n" % (key, response.headers[key]))
        #out_list.append("\r\n")
        #content = response.text
        #if isinstance(content, memoryview):
            #content = content.tobytes()
        #out_list.append(content)
#
        #xlog.debug("%s", "".join(out_list))
#
        #self.wfile.write("".join(out_list))

# called by smart_router
#def wrap_ssl(sock, host, port, client_address):
    #certfile = CertUtil.get_cert(host or 'www.google.com')
    #ssl_sock = ssl.wrap_socket(sock, keyfile=CertUtil.cert_keyfile,
                               #certfile=certfile, server_side=True)
    #return ssl_sock


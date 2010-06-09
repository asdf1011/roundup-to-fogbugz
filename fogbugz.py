
import httplib
import random
import string
from StringIO import StringIO
import sys
import urllib
import urlparse
from xml.etree import ElementTree
from xml.parsers.expat import ExpatError

class FogbugzConnection:
    def __init__(self, hostaddress=None):
        self._connection = None
        if hostaddress is None:
            # Override the post with a test implementation.
            print 'IMPORTANT: Fogbugz server not specifed! Printing issues to stdout.'
            self._post = self._test_post
            self._root_path = 'test://server/'
            username = None
            password = None
        else:
            server = urlparse.urlparse(hostaddress)
            username = server.username
            password = server.password
            while not username:
                username = raw_input('Fogbugz username: ')
            while not password:
                password = getpass.getpass('Enter Fogbugz admin password:')

            if not server.scheme or server.scheme == 'http':
                self.connection = httplib.HTTPConnection(server.hostname, server.port)
            elif server.scheme == 'https':
                self.connection = httplib.HTTPSConnection(server.hostname, server.port)
            else:
                sys.exit("Unknown server scheme '%s'!" % server.scheme)

            # Request the 'live' url
            self._root_path = server.path
            self.connection.request('GET', self._root_path + '/api.xml')
            self._http_path = '/%s' % self._get_element(self._get_response(), 'url').text

        self._token = None
        self._token = self.post('logon', {'email':username, 'password':password}, element='token').text

    def _test_post(self, args, files=[]):
        cmd = args['cmd']
        if cmd == 'logon':
            return '<response><token>%i</token></response>' % random.randint(0, 1000)
        elif cmd == 'newProject':
            return '<response><project><ixProject>%i</ixProject></project></response>' % random.randint(0, 1000)
        elif cmd == 'newPerson':
            return '<response><person><ixPerson>%i</ixPerson></person></response>' % random.randint(0, 1000)
        elif cmd == 'new':
            return '<response><case ixBug="%i" /></response>' % random.randint(0, 1000)
        elif cmd in ['edit', 'close', 'resolve', 'reactivate']:
            return '<response><case ixBug="1234" /></response>'
        else:
            raise Exception('%s not handled in test...' % cmd)

    def _post(self, args, files):
        """Post a request to the fogbugz server."""
        return self._post_multipart("POST", self._http_path, args.items(),
                [('File%i' % (i+1), name, contents) for i, (name, contents) in enumerate(files)])

    def post(self, cmd, args, files=[], element=None):
        """Post a single change to fogbugz.

        cmd -- The command to run (eg: edit, close, reopen, ...).
        files -- A list of (filename, contents) tuples.
        element -- An xml selector to return.
        return -- An ElementTree instance for the FogBugz result. 
        """
        if self._token is not None:
            args['token'] = self._token
        if files:
            args['nFileCount'] = len(files)
        args['cmd'] = cmd
        xml = self._post(args, files)
        return self._get_element(xml, element)

    def get_attachment(self, path):
        url = self._root_path + path + '&token=' + self._token
        print 'Asking for attachment at', url
        if self._post is not self._test_post:
            self.connection.request('GET', url)
            return self._get_response()
        else:
            print 'Asked for attachment %s' % url
            return ''

    def _get_element(self, xml, element):
        try:
            tree = ElementTree.parse(StringIO(xml)).getroot()
        except ExpatError, ex:
            print xml
            sys.exit(str(ex))
        if tree.find('error') is not None:
            sys.exit(xml)
        if element is not None:
            tree = tree.find(element)
            if tree is None:
                sys.exit("Failed to find element '%s' in:\n%s" % (element, xml))
        return tree;

    def _get_response(self):
        response = self.connection.getresponse()
        if response.status != 200:
            sys.exit('Fogbugz server failure %i: %s' % (response.status, response.reason))
        return response.read()

    def _post_multipart(self, host, selector, fields, files):
        content_type, body = self._encode_multipart_formdata(fields, files)
        self.connection.putrequest('POST', selector)
        self.connection.putheader('content-type', content_type)
        self.connection.putheader('content-length', len(body))
        self.connection.endheaders()
        self.connection.send(body)
        return self._get_response()

    def _encode_multipart_formdata(self, fields, files):
        boundary = '----------' + ''.join(
                string.ascii_letters[random.randint(0, len(string.ascii_letters)-1)]
                for i in range(16))
        CRLF = '\r\n'
        L = []
        for (key, value) in fields:
            L.append('--' + boundary)
            L.append('Content-Disposition: form-data; name="%s"' % key)
            L.append('')
            L.append(value)
        for (key, filename, value) in files:
            L.append('--' + boundary)
            L.append('Content-Disposition: form-data; name="%s"; filename="%s"' % (key, filename))
            L.append('Content-Type: application/octet-stream')
            L.append('')
            L.append(value)
        L.append('--' + boundary + '--')
        L.append('')
        body = CRLF.join(str(l) for l in L)
        content_type = 'multipart/form-data; boundary=%s' % boundary
        return content_type, body


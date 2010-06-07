#!/usr/bin/env python

from collections import namedtuple
import csv
import datetime
import httplib
import getpass
from optparse import OptionParser
import os.path
import random
import string
from StringIO import StringIO
import sys
import urllib
import urlparse
from xml.etree import ElementTree
from xml.parsers.expat import ExpatError

doc = '''%s [options] <roundup export directory>
Import a roundup issue archive into a fogbugz database.

This program will;
* Import all roundup users.
* Import the history for all issues, including;
   - Title
   - Messages
   - Priority
   - Status
   - Assigned to
   - Keywords
   - Attachments
* Can optionally allow mapping of keywords to projects (in which case it will
  create those projects, assign the bug to the project, and remove the
  keyword from the tags).
* Attempt to keep the same issue numbers.

It will not;
* Import passwords.
* Keep the nosy list (this wasn't required for us, but it'll be easy to add).
* Remove messages from issues (supported by roundup, but not by fogbugz).
* Import non-default classes (should be easy to customise though).''' % sys.argv[0]


def load_class(dir, name):
    """Load the current state of a class from the issues csv.

    Returns a list of dictionary's, one for each issue. Each dictionary is the
    (name:value) for each field in the class, and repr."""
    filename = os.path.join(dir, '%s.csv' % name)
    contents = csv.reader(open(filename), delimiter=':')
    Class = namedtuple(name, (h.replace(' ', '_') for h in contents.next()))
    for row in contents:
        yield Class(*(eval(c) for c in row))

def load_journal(dir, name):
    '''Load the journal for a given class.

    Returns a dictionary of (id: (timestamp, user, action, contents))'''
    filename = os.path.join(dir, '%s-journals.csv' % name)
    Change = namedtuple('Change', ['id', 'timestamp', 'user_id', 'action', 'items'])
    changes = (Change(*(eval(f) for f in c)) for c in csv.reader(open(filename), delimiter=':'))
    result = {}
    for change in changes:
        result.setdefault(change.id, []).append(change)
    return result

def _reverse_history(item, journal):
    '''Query the history of a given instance.

    Returns a list of (timestamp, userid, item) tuples.'''
    # Walk backwards over the history, as we have the current state and want
    # to reproduce the initial state. Roundup export archives store the latest
    # version of each class, then stores the changes made to get to that
    # version in the journal.
    #
    # To walk over history, we start with the latest version, then walk
    # backwards, at each stage yielding the 'latest' before undoing the changes
    # that made it that way.
    changes = journal[item.id]
    changes.reverse()
    for change in changes:
        item = item._replace(activity=change.timestamp, actor=change.user_id)
        yield item

        # Now undo the changes that made it that way.
        for field, mods in change.items.items():
            if isinstance(mods, tuple):
                # We are changing a list... add or remove the entires as appropriate.
                for type, values in mods:
                    if type == '+':
                        item = item._replace(**{field: [v for v in getattr(item, field) if v not in values]})
                    elif type == '-':
                        item = item._replace(**{field: values + getattr(item, field)})
                    else:
                        raise Exception('Unhandled change %s - %s!' % (type, values))
            else:
                item = item._replace(**{field:mods})
        yield item

def mktime(timestamp):
    # The time tuple seconds is a float, but we need separate seconds & milliseconds
    time_tuple = list(timestamp[:6])
    time_tuple.append(int(time_tuple[-1] % 1 * 1000000))
    time_tuple[-2] = int(time_tuple[-2])
    return datetime.datetime(*time_tuple)

def history(item, journal):
    # Roundup has multiple journal entries for one unique state; if we find two
    # entries very close in time to each other, collapse them.
    items = list(_reverse_history(item, journal))
    previous = None
    for item in items:
        current = mktime(item.activity)
        if not previous or previous - current > datetime.timedelta(0, 0, 500000):
            yield item
        previous = current


class FogbugzConnection:
    def __init__(self, hostaddress=None):
        self._connection = None
        if hostaddress is None:
            # Override the post with a test implementation.
            print 'IMPORTANT: Fogbugz server not specifed! Printing issues to stdout.'
            self._post = self._test_post
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
            self.connection.request('GET', server.path)
            self._http_path = '/%s' % self._get_element(self._get_response(), 'url').text

        self._token = None
        self._token = self.post('logon', {'email':username, 'password':password}, element='token').text

    def _test_post(self, args, files=[]):
        print 'Posting', args, files
        cmd = args['cmd']
        if cmd == 'logon':
            return '<response><token>%i</token></response>' % random.randint(0, 1000)
        elif cmd == 'newProject':
            return '<response><ixProject>%i</ixProject></response>' % random.randint(0, 1000)
        elif cmd == 'newPerson':
            return '<response><ixPerson>%i</ixPerson></response>' % random.randint(0, 1000)
        elif cmd == 'new':
            return '<response><ixBug>%i</ixBug></response>' % random.randint(0, 1000)
        elif cmd == 'edit':
            return '<response/>'
        else:
            raise Exception('%s not handled in test...' % cmd)

    def _post(self, args, files):
        """Post a request to the fogbugz server."""
        return self._post_multipart("POST", self._http_path, args.items(),
                [('File%i' % (i+1), name, open(path, 'r').read()) for i, (name, path) in enumerate(files)])

    def post(self, cmd, args, files=[], element=None):
        if self._token is not None:
            args['token'] = self._token
        if files:
            args['nFileCount'] = len(files)
        args['cmd'] = cmd
        print args
        xml = self._post(args, files)
        return self._get_element(xml, element)

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
        self.connection.putheader('content-length', str(len(body)))
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
        body = CRLF.join(L)
        content_type = 'multipart/form-data; boundary=%s' % boundary
        return content_type, body


class Lookup (dict):
    def __init__(self, name):
        self.name = name
        self.default = None

    def __getitem__(self, name):
        try:
            return dict.__getitem__(self, name)
        except KeyError:
            if name is not None:
                raise
            if self.default is None:
                sys.exit("Failed to find '%s' in %s lookup. Use the command line to specify a default." % (name, self.name))
            return self.default


def get_tags(keywords, keyword_lookup, project_lookup):
    project = None
    tags = []
    for keyword in keywords:
        try:
            project = project_lookup[keyword]
        except KeyError:
            tags.append(keyword_lookup[keyword])
    if project is None:
        # There wasn't a tag that identified a project, so use the default.
        project = project_lookup[None]
    return project, tags

class FogbugzUsers:
    """ A class to create users in the fogbugz database on demand."""
    def __init__(self, users, defaultUserName, connection):
        self._connection = connection
        self._users = users
        self._lookup = {}

        # Set all unassigned issues to the requested user
        self._default_user_id = None
        if defaultUserName:
            for user in users:
                if user.realname == defaultUserName:
                    self._default_user_id = user.id
                    break
            else:
                sys.exit("Unable to find user name '%s' to be unassigned user!" % options.unassigned_user)

    def get_ixperson(self, roundupId):
        if roundupId is None:
            if self._default_user_id is None:
                sys.exit('No default user found, but one is required. Use the command line to specify a default.')
            roundupId = self._default_user_id

        try:
            return self._lookup[roundupId]
        except KeyError:
            # We haven't asked for this user yet...
            pass

        for user in self._users:
            if user.id == roundupId:
                self._lookup[user.id] = self._connection.post('newPerson', {
                    'sEmail':user.address,
                    'sFullname':user.realname,
                    'fActive':(1 if not user.is_retired else 0),
                    }, element='ixPerson').text
                return self._lookup[user.id]
        sys.exit("Failed to find user with id '%s'." % (roundupId))

def fogbugz_user_upload(users, connection):
    """Upload users to fogbugz.

    Returns a map of {roundup_user_id: ixPerson}"""
    result = Lookup('users')
    for user in users:
        result[user.id] = connection.post('newPerson', {
            'sEmail':user.address,
            'sFullname':user.realname,
            'fActive':(1 if not user.is_retired else 0),
            }, element='ixPerson').text
    return result

def fogbugz_issue_upload(issue_history, users, message_lookup,
        keyword_lookup, project_lookup, file_lookup, connection):
    """Upload issue changes to fogbugz."""
    cmd = 'new'
    ixbug = None
    params = {}
    existing_messages = []
    existing_files = []
    for issue in issue_history:
        project_id, tags = get_tags(issue.keyword, keyword_lookup, project_lookup)

        if ixbug is not None:
            params['ixBug'] = ixbug
        params['sTags'] = ','.join(tags)
        params['sTitle'] = issue.title
        params['ixProject'] = project_id
        # params['ixCategory'] = 
        params['ixPersonAssignedTo'] = users.get_ixperson(issue.assignedto)
        params['ixPersonEditedBy'] = users.get_ixperson(issue.actor)
        params['dt'] = str(mktime(issue.activity))

        # Check for new messages
        message_ids = [id for id in issue.messages if id not in existing_messages]
        if len(message_ids) > 1:
            raise Exception('Got multiple new messages in the same changeset! %s - %s' % (issue, message_ids))
        elif len(message_ids) == 1:
            params['sEvent'] = message_lookup[message_ids[0]]
        existing_messages += message_ids

        # Check for new files
        files = [file_lookup[id] for id in issue.files if id not in existing_files]
        existing_files += [id for id in issue.files]
        response = connection.post(cmd, params, files)
        if ixbug is None:
            ixbug = response.find('ixBug').text
        cmd = 'edit'

def fogbugz_create_projects(keywords, mapping, default_project, connection):
    result = Lookup('projects')
    names = {}
    if mapping:
        mapping = [m.split(':') for m in mapping]
        for keyword, project in mapping:
            id = dict((name, id) for id, name in keywords.items())[keyword]
            result[id] = connection.post('newProject', {'sProject':project}, element='ixProject').text
            names[project] = result[id]
    if default_project is not None:
        try:
            result.default = names[default_project]
        except KeyError:
            # There isn't a project named after the default project; create it now.
            print "There isn't a project named '%s' to use as the default; making it too." % default_project
            result.default = connection.post('newProject', {'sProject':default_project}, element='ixProject').text
    return result

def main():
    parser = OptionParser(usage=doc)
    parser.add_option('--map' ,help="Map a roundup keyword to a project name. " \
            "If it finds the given tag in an issue, it will remove that keyword, "
            "and assign the issue to the given project.", metavar="KEYWORD:PROJECT",
            action='append')
    parser.add_option('--default-project', help="Set the default project for all "
            "issues that don't have a keyword specified in '--map'.", metavar="PROJECT")
    parser.add_option('--fogbugz-server', help="Set the fogbugz server. eg: "
            "'http://username:password@127.0.0.1:7006/fogbugz/api.xml'. If not "
            "specified the issues will be printed to stdout, and not imported "
            "to fogbugz. If the username and password is not specified, the "
            "user will be prompted to enter it.", metavar="ADDRESS")
    parser.add_option('--unassigned-user', help="Set the roundup user who will be "
            "set as the owner of all unassigned bugs (as fogbugz requires a user "
            "for all bugs).", metavar="REAL_NAME")
    options, args = parser.parse_args()
    if len(args) != 1:
        sys.exit("Missing roundup export directory argument! See '%s -h' for more info." % sys.argv[0])
    directory = args[0]

    connection = FogbugzConnection(options.fogbugz_server)

    # Load the support classes
    roundupUsers = list(load_class(directory, 'user'))
    message_lookup = dict((message.id,
        file(os.path.join(directory, 'msg-files', str(int(message.id) / 1000), 'msg%s' % message.id), 'r').read())
        for message in load_class(directory, 'msg'))
    keyword_lookup = dict((keyword.id, keyword.name) for keyword in load_class(directory, 'keyword'))

    # Upload the projects
    print 'uploading users...'
    users = FogbugzUsers(roundupUsers, options.unassigned_user, connection)

    # Check the keyword -> project mapping
    project_lookup = fogbugz_create_projects(keyword_lookup, options.map,
            options.default_project, connection)

    # Load the issues
    issues = list(load_class(directory, 'issue'))
    issues.sort(key=lambda i: int(i.id))
    journal = load_journal(directory, 'issue')

    # Load the files
    file_lookup = dict((file.id, (file.name,
        os.path.join(directory, 'file-files', '0', 'file%s' % file.id)))
        for file in load_class(directory, 'file'))

    for issue in issues:
        print 'uploading issue %s...' % issue.id
        changes = list(history(issue, journal))
        changes.reverse()
        fogbugz_issue_upload(changes, users, message_lookup,
                keyword_lookup, project_lookup, file_lookup, connection)

if __name__ == '__main__':
    main()


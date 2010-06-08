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
import sys
import urllib
import urlparse

from fogbugz import FogbugzConnection

doc = '''%s [options] <roundup export directory>
Import a roundup issue archive into a fogbugz database.''' % sys.argv[0]

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
            tags.append(keyword_lookup[keyword].replace(' ', '-'))
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

        self._default_user_id = None
        if defaultUserName:
            for user in users:
                if user.realname == defaultUserName:
                    self._default_user_id = user.id
                    break
            else:
                sys.exit("Unable to find user name '%s' to be default user!" % defaultUserName)

    def get_ixperson(self, roundupId):
        if roundupId is None:
            if self._default_user_id is None:
                sys.exit('No default user found, but one is required. Use the command line to specify a default.')
            roundupId = self._default_user_id

        try:
            return self._lookup[roundupId]
        except KeyError:
            pass

        # We haven't create for this user yet... do so now.
        for user in self._users:
            if user.id == roundupId:
                self._lookup[user.id] = self._connection.post('newPerson', {
                    'sEmail':user.address,
                    'sFullname':user.realname,
                    'fActive':(1 if not user.is_retired else 0),
                    }, element='person/ixPerson').text
                return self._lookup[user.id]
        sys.exit("Failed to find user with id '%s'." % (roundupId))

def fogbugz_issue_upload(issue_history, users, message_lookup,
        keyword_lookup, project_lookup, file_lookup, status_lookup,
        priority_lookup, connection):
    """Upload issue changes to fogbugz."""
    roundup_priority = dict((name, id) for (id, name) in priority_lookup.items())
    fogbugz_priority = {
            'critical' : 1,
            'urgent' : 2,
            'bug' : 3,
            'feature' : 4,
            'wish' : 5,
            }
    ixbug = None
    existing_messages = []
    existing_files = []
    for issue in issue_history:
        project_id, tags = get_tags(issue.keyword, keyword_lookup, project_lookup)

        params = {}
        if ixbug is None:
            cmd = 'new'
        else:
            params['ixBug'] = ixbug
            if issue.status is None or status_lookup[issue.status] == 'resolved':
                cmd = 'resolve'
            elif cmd == 'resolve':
                cmd = 'reactivate'
            else:
                cmd = 'edit'

        params['sTags'] = ','.join(tags)
        params['sTitle'] = issue.title
        params['ixProject'] = project_id
        # params['ixCategory'] = 
        if issue.assignedto is None:
            params['ixPersonAssignedTo'] = users.get_ixperson(issue.creator)
        else:
            params['ixPersonAssignedTo'] = users.get_ixperson(issue.assignedto)
        params['ixPersonEditedBy'] = users.get_ixperson(issue.actor)
        params['dt'] = mktime(issue.activity)
        params['ixPriority'] = fogbugz_priority[priority_lookup[issue.priority]]

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
        response = connection.post(cmd, params, files, 'case')
        if ixbug is None:
            ixbug = response.attrib['ixBug']

    if cmd == 'resolve':
        # If the final status is resolved, assume it has been fixed
        connection.post('close', {'ixBug':ixbug})


def fogbugz_create_projects(keywords, mapping, default_project, users, connection):
    result = Lookup('projects')
    names = {}
    if mapping:
        mapping = [m.split(':') for m in mapping]
        for keyword, project in mapping:
            id = dict((name, id) for id, name in keywords.items())[keyword]
            result[id] = connection.post('newProject', {
                'sProject':project,
                'ixPersonPrimaryContact':users.get_ixperson(None),
                },
                element='project/ixProject').text
            names[project] = result[id]
    if default_project is not None:
        try:
            result.default = names[default_project]
        except KeyError:
            sys.exit("There isn't a tag named after the default project!")
    return result

def _create_placeholder_bug(project_lookup, users, connection):
    """Create an closed placeholder bug to remove the missing bug id."""
    params = {
            'ixProject': project_lookup[None],
            'ixPersonAssignedTo': users.get_ixperson(None),
            'sTitle': 'Placeholder bug to take into account a missing roundup bug id.',
            }
    params['ixBug'] = connection.post('new', params, [], 'case').attrib['ixBug']
    connection.post('resolve', params, [])
    connection.post('close', params, [])

def main():
    parser = OptionParser(usage=doc)
    parser.add_option('--map' ,help="Map a roundup keyword to a project name. " \
            "If it finds the given tag in an issue, it will remove that keyword, "
            "and assign the issue to the given project.", metavar="KEYWORD:PROJECT",
            action='append')
    parser.add_option('--default-project', help="Set the default project for all "
            "issues that don't have a keyword specified in '--map'.", metavar="PROJECT")
    parser.add_option('--fogbugz-server', help="Set the fogbugz server. eg: "
            "'http://username:password@127.0.0.1:7006/fogbugz/'. If not "
            "specified the issues will be printed to stdout, and not imported "
            "to fogbugz. If the username and password is not specified, the "
            "user will be prompted to enter it.", metavar="ADDRESS")
    parser.add_option('--default-user', help="Set the roundup user who will be "
            "set as the owner of all projects.", metavar="REAL_NAME")
    parser.add_option('--disable-placeholder-bugs', help="By default the tool will "
            "create placeholder issues to keep the roundup to fogbugz ids "
            "syncronised. This flag will disable the creation of placeholder issues.",
            action='store_true')
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
    users = FogbugzUsers(roundupUsers, options.default_user, connection)

    # Check the keyword -> project mapping
    project_lookup = fogbugz_create_projects(keyword_lookup, options.map,
            options.default_project, users, connection)

    # Load the issues
    issues = list(load_class(directory, 'issue'))
    issues.sort(key=lambda i: int(i.id))
    journal = load_journal(directory, 'issue')
    status_lookup = dict((s.id, s.name) for s in load_class(directory, 'status'))
    priority_lookup = dict((p.id, p.name) for p in load_class(directory, 'priority'))

    # Load the files
    file_lookup = dict((file.id, (file.name,
        os.path.join(directory, 'file-files', '0', 'file%s' % file.id)))
        for file in load_class(directory, 'file'))

    i = 1
    for issue in issues:
        if not options.disable_placeholder_bugs:
            while int(issue.id) > i:
                print 'Creating dummy bug to skip issue %i...' % i
                _create_placeholder_bug(project_lookup, users, connection)
                i += 1
            assert int(issue.id) == i, 'Expected issue with id %i, got %s' % (i, issue.id)
            i = int(issue.id) + 1

        print 'uploading issue %s...' % issue.id
        changes = list(history(issue, journal))
        changes.reverse()
        fogbugz_issue_upload(changes, users, message_lookup,
                keyword_lookup, project_lookup, file_lookup, status_lookup,
                priority_lookup, connection)

if __name__ == '__main__':
    main()


#!/usr/bin/env python

import logging
from optparse import OptionParser
import sys

from fogbugz.connection import Connection, MockConnection
from fogbugz.export import get_issues, ExportError, Users, Projects

doc = '''%s [options] <source_url> [dest_url]
Migrate from a fogbugz database to another fogbugz database.

This migration uses the api.xml interface, not the database dump mechanism
supported by fogbugz. Both source_url and dest_url are of the form;

  http://user:password@example.com/bugs/

If the destination url is not specified, it will just project the operations
to be performed to stdout.
''' % sys.argv[0]


def _get_commands(source, users, projects, search):
    """Returns a list of (cmd, params, files) tuples."""
    for issue in get_issues(source, search):
        cmd = None
        issue.reverse()
        for change in issue:
            status = change.pop('sStatus')
            if cmd is None:
                cmd = 'new'
            elif status == 'Active':
                cmd = 'edit'
            elif status.startswith('Resolved'):
                cmd = 'resolve'
            elif status.startswith('Closed'):
                cmd = 'close'
            else:
                raise ExportError('Unknown status %s!' % status)
            files = change.pop('attachments')
            # There is a bug in the api.xml that escapes the '&' characters
            # in the url, despite being in a CDATA section. Work around this
            # by unescaping it (again).
            files = [(filename, url.replace('&amp;', '&')) for filename, url in files]

            yield (cmd, change, files)

def migrate(source, dest, users, projects, search):
    # We load all of the changes, and insert them according to timestamp. This
    # ensures the parent bugs are created before the children.
    changes = list(_get_commands(source, users, projects, search))

    # We sort by timestamp first, as we want to replay the events in order (to
    # handle dependencies between the bugs), but then by bug id, as we want
    # the lower ixBugs before the higher ixBugs when they have the same
    # timestamp. This is necessary because fogbugz will create parents & children
    # at the same timestamp...
    changes.sort(key=lambda change:(change[1]['dt'], int(change[1]['ixBug'])))

    ixBugLookup = {}
    for i, (cmd, params, files) in enumerate(changes):
        logging.info('Migrating change %i of %i (bug %s at %s)', i + 1, len(changes), params['ixBug'], params['dt'])
        editor = params.pop('ixPerson')
        if editor != '-1':
            # The '-1' user is the email user, but we can't import that (as
            # fogbugz will complain that 'Person #-1 does not exist.'.
            params['ixPersonEditedBy'] = users.get_ixperson(editor)
        params['ixProject'] = projects.get_ixproject(params.pop('sProject'))
        params['tags'] = ','.join(params.pop('tags'))
        parentBug = params.pop('ixBugParent')
        if parentBug != '0':
            logging.debug('setting parent of %s to %s', params['ixBug'], parentBug)
            if parentBug is not None:
                params['ixBugParent'] = ixBugLookup[parentBug]
            else:
                params['ixBugParent'] = '(None)'

        files = [(filename, source.get_attachment(url)) for filename, url in files]
        ixBug = params.pop('ixBug')
        if cmd != 'new':
            params['ixBug'] = ixBugLookup[ixBug]
        response = dest.post(cmd, params, files, 'case')
        if cmd == 'new':
            ixBugLookup[ixBug] = response.attrib['ixBug']

def main():
    parser = OptionParser(usage=doc)
    parser.add_option('--project' ,help="Map an existing fogbugz project to one in " \
            "target database.", metavar="PROJECT:PROJECT", action='append', default=[])
    parser.add_option('--search' ,help="Only migrate issues that are present "
            "in the given search (eg: '-tag:ignore'). By default it will use the "
            "user's default search, which is typically all non-closed bugs.",
            metavar="STRING")
    parser.add_option('--user' ,help="Map an existing fogbugz user to one in " \
            "target database.", metavar="USER:USER", action='append', default=[])
    parser.add_option('--verbose', help='Verbose logging.', action='store_true')
    options, args = parser.parse_args()

    logging.basicConfig(level=(logging.DEBUG if options.verbose else logging.INFO))

    if len(args) == 0:
        sys.exit("Missing source url. See '%s -h' for more info." % sys.argv[0])
    elif len(args) == 1:
        dest = MockConnection(name='destination')
    elif len(args) == 2:
        dest = Connection(args[1], name='destination')
    elif len(args) > 2:
        sys.exit("Too many arguments. See '%s -h' for more info." % sys.argv[0])
    source = Connection(args[0], name='source')

    users = Users(dict(u.split(':') for u in options.user), source, dest)
    projects = Projects(dict(p.split(':') for p in options.project), users, source, dest)

    migrate(source, dest, users, projects, options.search)
    logging.info('done.')

if __name__ == '__main__':
    main()

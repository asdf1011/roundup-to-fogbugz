#!/usr/bin/env python

#   Copyright (C) 2010 Henry Ludemann <misc@hl.id.au>
#
#   This file is part of the fogbugz import/export library.
#
#   The fogbugz import/export library is free software; you can redistribute it
#   and/or modify it under the terms of the GNU Lesser General Public
#   License as published by the Free Software Foundation; either
#   version 2.1 of the License, or (at your option) any later version.
#
#   The fogbugz import/export library is distributed in the hope that it will be
#   useful, but WITHOUT ANY WARRANTY; without even the implied warranty
#   of MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
#   Lesser General Public License for more details.
#
#   You should have received a copy of the GNU Lesser General Public
#   License along with this library; if not, see
#   <http://www.gnu.org/licenses/>.


import logging
from optparse import OptionParser
import sys

from fogbugz.connection import Connection, MockConnection
from fogbugz.export import get_issues, ExportError, dict_from_element

doc = '''%s [options] <source_url> [dest_url]
Migrate from a fogbugz database to another fogbugz database.

This migration uses the api.xml interface, not the database dump mechanism
supported by fogbugz. Both source_url and dest_url are of the form;

  http://user:password@example.com/bugs/

If the destination url is not specified, it will just project the operations
to be performed to stdout.
''' % sys.argv[0]

class Mapping:
    """ A class to map a table from one project to another."""
    def __init__(self, mapping, ix_name, name, additional_columns, list_cmd,
            new_cmd, xml_search, xml_name_search, source, dest):
        self._lookup = {}
        self._destConnection = dest
        self._source = source

        self._name = name
        self._new_cmd = new_cmd
        self._xml_search = xml_search
        self._xml_name_search = xml_name_search
        self._ix_name = ix_name
        self._list_cmd = list_cmd

        # Map the user specified source users to the destination users
        self._columns = [ix_name, name] + additional_columns
        self._source_items = [dict_from_element(e, self._columns)
                for e in source.post(list_cmd, {}).findall(xml_search)]

        source_names = dict((p[name], p[ix_name])
            for p in self._source_items)
        dest_names = dict((p.find(name).text, p.find(ix_name).text)
            for p in dest.post(list_cmd, {}).findall(xml_search))

        for source_name, dest_name in mapping.items():
            try:
                source_ix = source_names[source_name]
            except KeyError:
                sys.exit("Failed to find source %s '%s'! Names are:\n%s" %
                        (name, source_name, '\n'.join(source_names.keys())))

            try:
                dest_ix = dest_names[dest_name]
            except KeyError:
                sys.exit("Failed to find dest %s '%s'! Names are:\n%s" %
                        (name, dest_name, '\n'.join(dest_names.keys())))
            self._lookup[source_ix] = dest_ix

    def _modifyItem(self, item):
        return item

    def get_ix(self, source_ix):
        try:
            return self._lookup[source_ix]
        except KeyError:
            pass

        # This one hasn't been imported yet.
        for i in self._source_items:
            if i[self._ix_name] == source_ix:
                item = i.copy()
                del item[self._ix_name]
                result = self._destConnection.post(self._new_cmd, self._modifyItem(item), element=self._xml_name_search).text
                logging.debug('Created %s: %s', self._new_cmd, result)
                self._lookup[source_ix] = result
                return result
        else:
            raise ExportError('Failed to find source %s with id %s! Ids are:\n%s' % (
                self._ix_name, source_ix,
                '\n'.join(s[self._ix_name] for s in self._source_items)))


class Users(Mapping):
    def __init__(self, user_map, source, dest):
        Mapping.__init__(self, user_map, 'ixPerson', 'sFullName',
                ['sEmail'],
                'listPeople', 'newPerson', 'people/person',
                'person/ixPerson', source, dest)

    def get_ixperson(self, ixperson):
        if ixperson == '-1':
            # This is a magic person, indicating fogbugz (ie: email). It shows
            # in the web interface as 'by FogBugz'.
            return ixperson
        return self.get_ix(ixperson)


class Projects(Mapping):
    def __init__(self, project_map, users, source, dest):
        # The get_ixproject asks with the sProject name, so map that accordingly.
        Mapping.__init__(self, project_map, 'ixProject', 'sProject',
                ['ixPersonOwner'],
                'listProjects', 'newProject', 'projects/project',
                'project/ixProject', source, dest)
        self._users = users
        self._source = source

    def get_ixproject(self, name):
        for project in self._source_items:
            if project['sProject'] == name:
                return self.get_ix(project['ixProject'])
        else:
            # Deleted projects are awkward; we know the name, but not enough
            # to recreate it
            logging.warning("Didn't find source project with name '%s'! Has it been " \
                    "deleted? Names are;\n%s", name,
                    ', '.join(p['sProject'] for p in self._source_items))
            logging.info('Stepping through projects on the server, attempting to find it...')
            for ixProject in range(100):
                ixProject = str(ixProject)
                logging.debug('Checking %s value %s...', self._ix_name, ixProject)
                for project in self._source.post('listProjects', {'ixProject':ixProject}).findall('projects/project'):
                    source_name = project.find('sProject').text
                    if name == source_name:
                        logging.info("Found project '%s'! Its ixProject is %s", name, ixProject)
                        self._source_items.append(dict_from_element(project, self._columns))
                        return self.get_ix(ixProject)
            raise ExportError('Unabled to find deleted source project!')

    def _modifyItem(self, item):
       # We need the destination user id, not the source.
       item['ixPersonPrimaryContact'] = self._users.get_ixperson(item.pop('ixPersonOwner'))
       return item


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
        assigned_to = params.pop('ixPersonAssignedTo')
        if assigned_to != '1':
            # The '1' user appears to be an internal fogbugz user that is
            # assigned closed bugs.
            params['ixPersonAssignedTo'] = users.get_ixperson(assigned_to)
        params['ixProject'] = projects.get_ixproject(params.pop('sProject'))
        params['sTags'] = ','.join(params.pop('tags'))
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

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

import os.path
import unittest

from fogbugz.connection import MockConnection
from fogbugz.export import get_issues

def connection(xml_filename):
    dir = os.path.dirname(__file__)
    filename = os.path.join(dir, xml_filename)
    return MockConnection(search=open(filename, 'r').read())

class TestFogbugzExport(unittest.TestCase):
    def test_tags_attachments(self):
        source = connection('tags_and_attachments.xml')

        issues = list(get_issues(source, None))
        self.assertEqual(1, len(issues))
        changes = issues[0]
        self.assertEqual(4, len(changes))
        self.assertEqual({'sTitle': 'Some Title', 'attachments': [],
            'tags': set(['jessie', 'rupert', 'sally']),
            'sStatus': 'Closed (Duplicate)',
            'ixPersonAssignedTo': '1', 'sCategory': 'Feature',
            'ixBugParent': '0', 'ixPriority': '1',
            'dt': '2010-04-20T10:19:52Z', 'sProject': 'Some Project',
            'ixPerson': '2', 'ixBug': '5'}, changes[0])
        self.assertEqual({'sTitle': 'Some Title', 'attachments': [],
            'tags': set(['jessie', 'rupert', 'sally']),
            'ixPersonAssignedTo': '1', 'sCategory': 'Feature',
            'ixBugParent': '0', 'ixPriority': '1', 'ixBug': '5',
            'dt': '2010-04-20T09:56:38Z', 'sProject': 'Some Project',
            'ixPerson': '2', 'sStatus': 'Resolved'}, changes[1])
        self.assertEqual({'sTitle': 'Some Title', 'attachments': [],
            'tags': set(['jessie', 'rupert', 'sally']), 'sStatus': 'Active',
            'ixPersonAssignedTo': '3', 'sCategory': 'Bug',
            'ixBugParent': '0', 'ixPriority': '1',
            'dt': '2010-03-13T00:52:11Z', 'sProject': 'Some Project',
            'ixPerson': '3', 'ixBug': '5'}, changes[2])
        self.assertEqual({'sTitle': 'Some Title',
            'attachments': [('VENUES.pdf', 'default.asp?pg=pgDownload&amp;pgType=pgFile&amp;ixBugEvent=15&amp;ixAttachment=3&amp;sFileName=VENUES.pdf&sTicket=')],
            'tags': set([]), 'sStatus': 'Active', 'ixPersonAssignedTo': '3',
            'sCategory': 'Bug', 'ixBugParent': '0', 'ixPriority': u'3',
            'dt': '2010-03-13T00:48:13Z', 'sProject': 'Some Project',
            'ixPerson': '3', 'ixBug': '5'}, changes[3])

    def test_resolve_and_close(self):
        source = connection('resolve_and_close.xml')

        issues = list(get_issues(source, None))
        self.assertEqual(1, len(issues))
        changes = issues[0]
        self.assertEqual(3, len(changes))
        self.assertEqual({'sTitle': 'Realtime ETL process development',
            'attachments': [], 'tags': set([]), 'sStatus': 'Closed (Duplicate)',
            'ixPersonAssignedTo': '1', 'sCategory': 'Feature',
            'ixBugParent': '46', 'ixPriority': '3',
            'dt': '2010-05-14T06:46:25Z', 'sProject': 'USA - Data',
            'ixPerson': '7', 'ixBug': '94'}, changes[0])
        self.assertEqual({'sTitle': 'Realtime ETL process development',
            'attachments': [], 'tags': set([]), 'ixPersonAssignedTo': '1',
            'sCategory': 'Feature', 'ixBugParent': '46', 'ixPriority': '3',
            'ixBug': '94', 'dt': '2010-05-14T06:46:25Z',
            'sProject': 'USA - Data', 'ixPerson': '7', 'sStatus': 'Resolved'},
            changes[1])
        self.assertEqual({'sTitle': 'Realtime ETL process development',
            'attachments': [], 'tags': set([]), 'ixPersonAssignedTo': '7',
            'sCategory': 'Feature', 'ixBugParent': '46', 'ixPriority': '3',
            'ixBug': '94', 'dt': '2010-05-14T06:45:33Z',
            'sProject': 'USA - Data', 'ixPerson': '7', 'sStatus': 'Active'},
            changes[2])


if __name__ == '__main__':
    import logging
    logging.basicConfig(level=(logging.DEBUG))
    unittest.main()

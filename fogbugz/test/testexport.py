
import os.path
import unittest

from fogbugz.connection import MockConnection
from fogbugz.export import get_issues


class TestFogbugzExport(unittest.TestCase):
    def test_tags_attachments(self):
        dir = os.path.dirname(__file__)
        filename = os.path.join(dir, 'tags_and_attachments.xml')
        source = MockConnection(search=open(filename, 'r').read())

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

if __name__ == '__main__':
    import logging
    logging.basicConfig(level=(logging.DEBUG))
    unittest.main()

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

from collections import namedtuple
import copy
import logging
import re
import sys

class ExportError (Exception):
    pass

def _update(issue, event, names):
    for name in names:
        issue[name] = event.find(name).text

def _dict_from_element(case, names):
    result = {}
    _update(result, case, names)
    return result

def _tag_handler(issue, action, tags):
    if action == 'Added tag':
        # We need to remove the added tags
        issue['tags'] -= set(t[1:-1] for t in tags.split(', '))
    elif action == 'Removed tag':
        # We need to re-add the removed tags
        issue['tags'].union(t[1:-1] for t in tags.split(', '))
    else:
        raise ExportError(issue, action, tags)

def _(regex, name):
    def result(issue, old, new):
        #assert issue[name] == new, "%s %s should have had title '%s'!" % (name, issue, new)
        issue[name] = old
    return regex, result

handlers = [(re.compile(regex), handler) for regex, handler in
        ('(?P<action>Added tag|Removed tag)s? (?P<tags>.*)\.', _tag_handler),
        _("Title changed from '(?P<old>.*)' to '(?P<new>.*)'\.", 'sTitle'),
        _("Priority changed from '(?P<old>\d*).*' to '(?P<new>\d*).*'", 'ixPriority'),
        _("Project changed from '(?P<old>.*)' to '(?P<new>.*)'", 'sProject'),
        _("Status changed from '(?P<old>.*)' to '(?P<new>.*)'", 'sStatus'),
        _("Category changed from '(?P<old>.*)' to '(?P<new>.*)'", 'sCategory'),
        _('Parent changed from Case (?P<old>.*) to Case (?P<new>.*)\.', 'ixBugParent'),
        ('Parent changed from Case (?P<old>.*) to \(None\)\.', lambda issue, old:issue.update([('ixBugParent', None)])),

        # We don't attempt to migrate all changes.
        ("Estimate .*", lambda issue:None),
        ("Milestone .*", lambda issue:None),
        ("Correspondent .*", lambda issue:None),
        ("Date due .*", lambda issue:None),
        ("Computer set to.*", lambda issue:None),

        # Not necessary; just tracking parents is enough.
        ("Added subcase.*", lambda issue:None),
        ("Removed subcase.*", lambda issue:None),
        ("Created subcase.*", lambda issue:None),
        ]

def _will_overwrite_changes(previous, current, next):
    """Check to see if there are any changes in 'next' that will overwrite those in 'current'."""
    for name, next_value in next.items():
        if name == 'attachments':
            # Attachments don't overwrite each other...
            continue
        previous_value = previous.get(name, None)
        if next_value != previous_value:
            # We have found a change between the previous and the next
            current_value = current.get(name, None)
            if current_value != previous_value and current_value != next_value:
                # There is a different change between previous and the current
                return True
    return False

def _has_changes(current, next):
    return _will_overwrite_changes({}, current, next)

def _is_different_timestamp(current, next):
    return current.get('dt') != next['dt']

def _changes(issue, events):
    # Fogbugz provides them to events in chronological order; we need to sort
    # the events in reverse chronological order, so we can walk backwards
    # recreating history.
    events.reverse()

    previous = {}
    issue['attachments'] = []
    current = copy.deepcopy(issue)
    for event in events:
        # As we are walking back in time, we are already in the state as
        # described in event. We get the timestamp and person who put us
        # in this state, then undo the changes described in event.
        assigned_to = issue['ixPersonAssignedTo']
        _update(issue, event, ['dt', 'ixPerson', 'ixPersonAssignedTo'])

        if issue['ixPersonAssignedTo'] == '0':
            # This seems to be a bug in the fogbugz export (it incorrectly sets
            # the 'assigned to' back to zero in later events).
            issue['ixPersonAssignedTo'] = assigned_to
        msg = event.find('s')
        if msg is not None and msg.text is not None:
            issue['sEvent'] = msg.text
        issue['attachments'].extend(
                (a.find('sFileName').text, a.find('sURL').text)
                for a in event.findall('rgAttachments/attachment'))

        if event.find('sVerb').text == 'Closed':
            # We don't get status notifications for this change.
            issue['sStatus'] = 'Resolved'
        change = event.find('sChanges').text
        if change:
            lines = [l.strip() for l in change.splitlines()]
            for line in lines:
                for regex, handler in handlers:
                    match = regex.match(line)
                    if match:
                        handler(issue, **match.groupdict())
                        break
                else:
                    raise ExportError(("Failed to find handler for '%s' in issue %s!" % (line, issue)).encode('ascii', 'ignore'))

        if _is_different_timestamp(current, issue) or _will_overwrite_changes(previous, current, issue):
            # We have undone the changes as described in the event, and they
            # are enough for us to report the change. We report the state we
            # were in before undoing the change at the timestamp of the
            # current event.
            current['dt'] = issue['dt']
            current['ixPerson'] = issue['ixPerson']
            if _has_changes(current, issue):
                yield current

            # Remove any attachments from the current issue we just reported,
            # as the won't be present in the previous change.
            uploads = [filename for filename, url in current['attachments']]
            issue['attachments'] = [(filename, url)
                    for filename, url in issue['attachments']
                    if filename not in uploads]
            previous = current
        current = issue
        issue = copy.deepcopy(issue)

    yield issue

def get_issues(source, search):
    columns = ['sProject', 'sTitle', 'ixPriority', 'ixBugParent', 'sStatus', 'sCategory', 'ixPersonAssignedTo', 'ixBug']
    params = {'cols':','.join(columns + ['tags','events'])}
    if search:
        params['q'] = search
    logging.info('Loading issues from database...')
    search_results = source.post('search', params)

    for case in search_results.findall('cases/case'):
        issue = _dict_from_element(case, columns)
        issue['tags'] = set(t.text for t in case.findall('tags/tag'))

        changes = []
        for change in _changes(issue, case.findall('events/event')):
            changes.append(change)
        yield changes


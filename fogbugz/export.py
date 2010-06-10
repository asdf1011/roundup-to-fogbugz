from collections import namedtuple
import copy
import logging
import re
import sys

class ExportError (Exception):
    pass

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
        self._source_items = [_dict_from_element(e, self._columns)
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
                        self._source_items.append(_dict_from_element(project, self._columns))
                        return self.get_ix(ixProject)
            raise ExportError('Unabled to find deleted source project!')

    def _modifyItem(self, item):
       # We need the destination user id, not the source.
       item['ixPersonPrimaryContact'] = self._users.get_ixperson(item.pop('ixPersonOwner'))
       return item


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
    return 'dt' in current and current['dt'] != next['dt']

def _changes(issue, events):
    from xml.etree.ElementTree import tostring
    timestamp = None
    # We need to sort the events in reverse chronological order, so we can
    # walk backwards recreating history.
    events.sort(key=lambda e:e.find('dt').text, reverse=True)

    previous = {}
    issue['attachments'] = []
    current = copy.deepcopy(issue)
    for event in events:
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
            # This event is enough to trigger a different changeset; report
            # the state as a change that took place at this time.
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


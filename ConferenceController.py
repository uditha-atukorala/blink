# Copyright (C) 2010-2011 AG Projects. See LICENSE for details.
#

from AppKit import (NSApp,
                    NSCancelButton,
                    NSDragOperationNone,
                    NSDragOperationGeneric,
                    NSLeftMouseUp,
                    NSOffState,
                    NSOKButton,
                    NSOnState,
                    NSRunAlertPanel)
from Foundation import (NSArray,
                        NSBundle,
                        NSDate,
                        NSEvent,
                        NSMenu,
                        NSMenuItem,
                        NSObject)
import objc

import cPickle
import random
import re
import shutil

from application.notification import NotificationCenter, IObserver
from application.python import Null
from application.system import makedirs
from resources import ApplicationData
from sipsimple.account import AccountManager, BonjourAccount
from sipsimple.configuration.settings import SIPSimpleSettings
from sipsimple.core import SIPCoreError, SIPURI
from zope.interface import implements

from SIPManager import SIPManager
from ConferenceConfigurationPanel import ConferenceConfigurationPanel
from util import allocate_autorelease_pool, run_in_gui_thread, sip_prefix_pattern


def random_room():
    return random.choice('123456789') + ''.join(random.choice('0123456789') for x in range(6))

default_conference_server = 'conference.sip2sip.info'


class ServerConferenceRoom(object):
    def __init__(self, target, media_type=None, participants=None, nickname=None, start_when_participants_available=False):
        self.target = target
        self.media_type = media_type
        self.participants = participants
        self.nickname = nickname
        self.start_when_participants_available = start_when_participants_available


class ConferenceConfiguration(object):
    def __init__(self, name, target, participants=None, media_type=None, nickname=None):
        self.name = name
        self.target = target
        self.participants = participants
        self.media_type = media_type
        self.nickname = nickname


def validateParticipant(uri):
    if not (uri.startswith('sip:') or uri.startswith('sips:')):
        uri = "sip:%s" % uri
    try:
        sip_uri = SIPURI.parse(str(uri))
    except SIPCoreError:
        return False
    else:
        return sip_uri.user is not None and sip_uri.host is not None


class JoinConferenceWindowController(NSObject):
    implements(IObserver)

    window = objc.IBOutlet()
    room = objc.IBOutlet()
    nickname_textfield = objc.IBOutlet()
    addRemove = objc.IBOutlet()
    participant = objc.IBOutlet()
    participantsTable = objc.IBOutlet()
    chat = objc.IBOutlet()
    audio = objc.IBOutlet()
    removeAllParticipants = objc.IBOutlet()
    configurationsButton = objc.IBOutlet()
    bonjour_server_combolist = objc.IBOutlet()
    ok_button = objc.IBOutlet()
    startWhenParticipantsAvailable = objc.IBOutlet()

    def __new__(cls, *args, **kwargs):
        return cls.alloc().init()

    def __init__(self, target=None, participants=[], media_type=["chat"], default_domain=None, autostart=False):
        NSBundle.loadNibNamed_owner_("JoinConferenceWindow", self)

        self.autostart = autostart
        self.notification_center = NotificationCenter()
        self.notification_center.add_observer(self, name='BonjourConferenceServicesDidRemoveServer')
        self.notification_center.add_observer(self, name='BonjourConferenceServicesDidUpdateServer')
        self.notification_center.add_observer(self, name='BonjourConferenceServicesDidAddServer')
        self.notification_center.add_observer(self, name='SIPAccountManagerDidChangeDefaultAccount')
        self.startWhenParticipantsAvailable.setEnabled_(False)

        self.selected_configuration = None

        self.default_domain = default_domain

        self.nickname = None

        if target is not None and "@" not in target and self.default_domain:
            target = '%s@%s' % (target, self.default_domain)

        if target is not None and validateParticipant(target):
            self.room.setStringValue_(target)

        account = AccountManager().default_account
        if account is not None:
            if account.conference.nickname:
                self.nickname_textfield.setStringValue_(account.conference.nickname)
            else:
                self.nickname_textfield.cell().setPlaceholderString_(account.display_name)

        if participants:
            self._participants = participants
        else:
            self._participants = []

        self.participantsTable.reloadData()
        self.removeAllParticipants.setHidden_(False if len(self._participants) > 1 else True)

        if media_type:
            self.audio.setState_(NSOnState if "audio" in media_type else NSOffState)
            self.chat.setState_(NSOnState if "chat" in media_type else NSOffState)

        self.updatePopupButtons()


    def dealloc(self):
        self.notification_center.remove_observer(self, name='BonjourConferenceServicesDidRemoveServer')
        self.notification_center.remove_observer(self, name='BonjourConferenceServicesDidUpdateServer')
        self.notification_center.remove_observer(self, name='BonjourConferenceServicesDidAddServer')
        self.notification_center.remove_observer(self, name='SIPAccountManagerDidChangeDefaultAccount')
        super(JoinConferenceWindowController, self).dealloc()

    @allocate_autorelease_pool
    @run_in_gui_thread
    def handle_notification(self, notification):
        handler = getattr(self, '_NH_%s' % notification.name, Null)
        handler(notification)

    def _NH_BonjourConferenceServicesDidRemoveServer(self, notification):
        self.updateBonjourServersPopupButton()

    def _NH_BonjourConferenceServicesDidUpdateServer(self, notification):
        self.updateBonjourServersPopupButton()

    def _NH_BonjourConferenceServicesDidAddServer(self, notification):
        self.updateBonjourServersPopupButton()

    def _NH_SIPAccountManagerDidChangeDefaultAccount(self, notification):
        self.room.setStringValue_('')
        self.nickname_textfield.setStringValue_('')
        account = AccountManager().default_account
        if account is not None:
            if account.conference.nickname:
                self.nickname_textfield.setStringValue_(account.conference.nickname)
            else:
                self.nickname_textfield.cell().setPlaceholderString_(account.display_name)
        self.updatePopupButtons()

    def loadConfigurations(self):
        path = ApplicationData.get('conference')
        makedirs(path)

        try:
            with open(ApplicationData.get('conference_configurations.pickle')): pass
        except IOError:
            pass
        else:
            src = ApplicationData.get('conference_configurations.pickle')
            dst = ApplicationData.get('conference/conference_configurations.pickle')
            try:
                shutil.move(src, dst)
            except shutil.Error:
                pass

        self.storage_path = ApplicationData.get('conference/conference_configurations.pickle')
        try:
            self.conference_configurations = cPickle.load(open(self.storage_path))
        except:
            self.conference_configurations = {}

    def updatePopupButtons(self):
        account = AccountManager().default_account
        if isinstance(account, BonjourAccount):
            self.configurationsButton.setHidden_(True)
            self.bonjour_server_combolist.setHidden_(False)
            self.updateBonjourServersPopupButton()
        else:
            self.configurationsButton.setHidden_(False)
            self.bonjour_server_combolist.setHidden_(True)
            self.loadConfigurations()
            self.updateConfigurationsPopupButton()
            self.ok_button.setEnabled_(True)

    @objc.IBAction
    def configurationsButtonClicked_(self, sender):
        if sender.selectedItem() == sender.itemWithTitle_(u"Save configuration..."):
            if self.validateConference(allow_random_room=False):
                if self.selected_configuration:
                    configuration_name = self.selected_configuration
                else:
                    configurationPanel = ConferenceConfigurationPanel.alloc().init()
                    configuration_name = configurationPanel.runModal()

                if self.audio.state() == NSOnState and self.chat.state() == NSOnState:
                    media_type = ("chat", "audio")
                elif self.chat.state() == NSOnState:
                    media_type = "chat"
                else:
                    media_type = "audio"

                if configuration_name:
                    if configuration_name in self.conference_configurations.keys():
                        self.conference_configurations[configuration_name].name = configuration_name
                        self.conference_configurations[configuration_name].target = self.target
                        self.conference_configurations[configuration_name].participants = self._participants
                        self.conference_configurations[configuration_name].media_type = media_type
                        self.conference_configurations[configuration_name].nickname = self.nickname
                    else:
                        configuration = ConferenceConfiguration(configuration_name, self.target, participants=self._participants, media_type=media_type, nickname=self.nickname)
                        self.conference_configurations[configuration_name] = configuration

                    self.selected_configuration = configuration_name
                    cPickle.dump(self.conference_configurations, open(self.storage_path, "w"))
            else:
                self.selected_configuration = None

        elif sender.selectedItem() == sender.itemWithTitle_(u"Rename configuration..."):
            configurationPanel = ConferenceConfigurationPanel.alloc().init()
            configuration_name = configurationPanel.runModalForRename_(self.selected_configuration)
            if configuration_name and configuration_name != self.selected_configuration:
                old_configuration = self.conference_configurations[self.selected_configuration]
                old_configuration.name = configuration_name
                self.conference_configurations[configuration_name] = old_configuration
                del self.conference_configurations[self.selected_configuration]
                self.selected_configuration = configuration_name
                cPickle.dump(self.conference_configurations, open(self.storage_path, "w"))

        elif sender.selectedItem() == sender.itemWithTitle_(u"Delete configuration") and self.selected_configuration:
           del self.conference_configurations[self.selected_configuration]
           cPickle.dump(self.conference_configurations, open(self.storage_path, "w"))
           self.setDefaults()
        else:
            configuration = sender.selectedItem().representedObject()
            if configuration:
                self.room.setStringValue_(configuration.target)
                try:
                    self.nickname_textfield.setStringValue_(configuration.nickname)
                except AttributeError:
                    self.nickname_textfield.setStringValue_('')
                self.selected_configuration = configuration.name
                self._participants = configuration.participants
                self.participantsTable.reloadData()
                self.removeAllParticipants.setHidden_(False if len(self._participants) > 1 else True)
                if hasattr(configuration, 'media_type'):
                    self.audio.setState_(NSOnState if "audio" in configuration.media_type else NSOffState)
                    self.chat.setState_(NSOnState if "chat" in configuration.media_type else NSOffState)
                else:
                    self.audio.setState_(NSOnState)
                    self.chat.setState_(NSOnState)
                self.startWhenParticipantsAvailable.setEnabled_(bool(len(self._participants)))
                if len(self._participants) == 0:
                    self.startWhenParticipantsAvailable.setState_(NSOffState)
            else:
                self.setDefaults()

        self.updateConfigurationsPopupButton()

    def updateConfigurationsPopupButton(self):
        self.configurationsButton.removeAllItems()
        if self.conference_configurations:
            self.configurationsButton.addItemWithTitle_(u"Select configuration")
            self.configurationsButton.lastItem().setEnabled_(False)
            self.configurationsButton.selectItem_(self.configurationsButton.lastItem())
            self.configurationsButton.addItemWithTitle_(u"None")
            self.configurationsButton.lastItem().setEnabled_(True)
            for key in self.conference_configurations.keys():
                self.configurationsButton.addItemWithTitle_(key)
                item = self.configurationsButton.lastItem()
                item.setRepresentedObject_(self.conference_configurations[key])
                if self.selected_configuration and self.selected_configuration == key:
                    self.configurationsButton.selectItem_(item)
        else:
            self.configurationsButton.addItemWithTitle_(u"No configurations saved")
            self.configurationsButton.lastItem().setEnabled_(False)

        self.configurationsButton.menu().addItem_(NSMenuItem.separatorItem())
        self.configurationsButton.addItemWithTitle_(u"Save configuration...")
        self.configurationsButton.lastItem().setEnabled_(True)
        self.configurationsButton.addItemWithTitle_(u"Rename configuration...")
        self.configurationsButton.lastItem().setEnabled_(True if self.selected_configuration else False)
        self.configurationsButton.addItemWithTitle_(u"Delete configuration")
        self.configurationsButton.lastItem().setEnabled_(True if self.selected_configuration else False)

    def updateBonjourServersPopupButton(self):
        settings = SIPSimpleSettings()
        account = AccountManager().default_account
        if isinstance(account, BonjourAccount):
            self.bonjour_server_combolist.removeAllItems()
            if SIPManager().bonjour_conference_services.servers:
                servers = set()
                servers_dict = {}
                for server in (server for server in SIPManager().bonjour_conference_services.servers if server.uri.transport in settings.sip.transport_list):
                    servers_dict.setdefault("%s@%s" % (server.uri.user, server.uri.host), []).append(server)
                for transport in (transport for transport in ('tls', 'tcp', 'udp') if transport in settings.sip.transport_list):
                    for k, v in servers_dict.iteritems():
                        try:
                            server = (server for server in v if server.uri.transport == transport).next()
                        except StopIteration:
                            pass
                        else:
                            servers.add(server)
                            break
                for server in servers:
                    self.bonjour_server_combolist.addItemWithTitle_('%s (%s)' % (server.host, server.uri.host))
                    item = self.bonjour_server_combolist.lastItem()
                    item.setRepresentedObject_(server)
                    self.ok_button.setEnabled_(True)
            else:
                self.bonjour_server_combolist.addItemWithTitle_(u"No SylkServer in this Neighbourhood")
                self.bonjour_server_combolist.lastItem().setEnabled_(False)
                self.ok_button.setEnabled_(False)
        else:
            self.ok_button.setEnabled_(False)

    def setDefaults(self):
        self.selected_configuration = None
        self.room.setStringValue_(u'')
        self.nickname_textfield.setStringValue_(u'')
        self._participants = []
        self.removeAllParticipants.setHidden_(True)
        self.participantsTable.reloadData()
        self.audio.setState_(NSOnState)
        self.chat.setState_(NSOnState)
        self.startWhenParticipantsAvailable.setEnabled_(False)
        self.startWhenParticipantsAvailable.setState_(NSOffState)

    def numberOfRowsInTableView_(self, table):
        try:
            return len(self._participants)
        except:
            return 0

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        try:
            return self._participants[row]
        except IndexError:
            return None

    def awakeFromNib(self):
        self.participantsTable.registerForDraggedTypes_(NSArray.arrayWithObjects_("x-blink-sip-uri"))

    @allocate_autorelease_pool
    def tableView_acceptDrop_row_dropOperation_(self, table, info, row, oper):
        pboard = info.draggingPasteboard()
        if pboard.availableTypeFromArray_(["x-blink-sip-uri"]):
            group, blink_contact = eval(pboard.stringForType_("dragged-contact"))
            if blink_contact is not None:
                sourceGroup = NSApp.delegate().contactsWindowController.model.groupsList[group]
                sourceContact = sourceGroup.contacts[blink_contact]

                if len(sourceContact.uris) > 1:
                    point = table.window().convertScreenToBase_(NSEvent.mouseLocation())
                    event = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                                                                                                                                              NSLeftMouseUp, point, 0, NSDate.timeIntervalSinceReferenceDate(), table.window().windowNumber(),
                                                                                                                                              table.window().graphicsContext(), 0, 1, 0)
                    invite_menu = NSMenu.alloc().init()
                    titem = invite_menu.addItemWithTitle_action_keyEquivalent_(u'Invite To Conference', "", "")
                    titem.setEnabled_(False)
                    for uri in sourceContact.uris:
                        titem = invite_menu.addItemWithTitle_action_keyEquivalent_('%s (%s)' % (uri.uri, uri.type), "addContactUriToInvitationList:", "")
                        titem.setIndentationLevel_(1)
                        titem.setTarget_(self)
                        titem.setRepresentedObject_(uri.uri)

                    NSMenu.popUpContextMenu_withEvent_forView_(invite_menu, event, table)
                    return True
                else:
                    participant = info.draggingPasteboard().stringForType_("x-blink-sip-uri")
                    self.addContactUriToInvitationList(participant)
                    return True
        return False

    def tableView_validateDrop_proposedRow_proposedDropOperation_(self, table, info, row, oper):
        if info.draggingPasteboard().availableTypeFromArray_(["x-blink-sip-uri"]):
            participant = info.draggingPasteboard().stringForType_("x-blink-sip-uri")
            if participant:
                participant = sip_prefix_pattern.sub("", str(participant))
            if participant and "@" not in participant and self.default_domain:
                participant = '%s@%s' % (participant, self.default_domain)
            if participant is None or not validateParticipant(participant):
                return NSDragOperationNone
            return NSDragOperationGeneric
        else:
            return NSDragOperationNone

    @objc.IBAction
    def addContactUriToInvitationList_(self, sender):
        participant = sender.representedObject()
        self.addContactUriToInvitationList(participant)

    def addContactUriToInvitationList(self, participant):
        if participant and "@" not in participant and self.default_domain:
            participant = '%s@%s' % (participant, self.default_domain)

        if participant:
            participant = sip_prefix_pattern.sub("", str(participant))

        try:
            if participant not in self._participants:
                self._participants.append(participant)
                self.startWhenParticipantsAvailable.setEnabled_(True)
                self.participantsTable.reloadData()
                self.removeAllParticipants.setHidden_(False if len(self._participants) > 1 else True)
                self.participantsTable.scrollRowToVisible_(len(self._participants)-1)
        except:
            pass

    def run(self):
        contactsWindow = NSApp.delegate().contactsWindowController.window()
        worksWhenModal = contactsWindow.worksWhenModal()
        contactsWindow.setWorksWhenModal_(True)
        if not self.autostart:
            self.window.makeKeyAndOrderFront_(None)
            rc = NSApp.runModalForWindow_(self.window)
        self.window.orderOut_(self)
        contactsWindow.setWorksWhenModal_(worksWhenModal)

        if (self.autostart and self.validateConference()) or rc == NSOKButton:
            if self.audio.state() == NSOnState and self.chat.state() == NSOnState:
                media_type = ("chat", "audio")
            elif self.chat.state() == NSOnState:
                media_type = "chat"
            else:
                media_type = "audio"

            # make a copy of the participants and reset the table data source,
            participants = self._participants

            # Cocoa crashes if something is selected in the table view when clicking OK or Cancel button
            # reseting the data source works around this
            self._participants = []
            self.participantsTable.reloadData()
            # prevent loops
            if self.target in participants:
                participants.remove(self.target)
            return ServerConferenceRoom(self.target, media_type=media_type, participants=participants, nickname=self.nickname, start_when_participants_available=bool(self.startWhenParticipantsAvailable.state()))
        else:
            return None

    @objc.IBAction
    def addRemoveParticipant_(self, sender):
        if sender.selectedSegment() == 0:
            participant = self.participant.stringValue().strip().lower()
            if participant:
                participant = sip_prefix_pattern.sub("", str(participant))
            self.addParticipant(participant)
        elif sender.selectedSegment() == 1:
            participant = self.selectedParticipant()
            if participant is None and self._participants:
                participant = self._participants[-1]
            if participant is not None:
                self._participants.remove(participant)
                self.startWhenParticipantsAvailable.setEnabled_(bool(len(self._participants)))
                if len(self._participants) == 0:
                    self.startWhenParticipantsAvailable.setState_(NSOffState)
                self.participantsTable.reloadData()

        self.removeAllParticipants.setHidden_(False if len(self._participants) > 1 else True)

    @objc.IBAction
    def removeAllParticipants_(self, sender):
        self._participants=[]
        self.participantsTable.reloadData()
        self.removeAllParticipants.setHidden_(True)

    def addParticipant(self, participant):
        if participant and "@" not in participant:
            participant = participant + '@' + self.default_domain

        if not participant or not validateParticipant(participant):
            NSRunAlertPanel("Add New Participant", "Participant must be a valid SIP address.", "OK", None, None)
            return

        if participant not in self._participants:
            self._participants.append(participant)
            self.startWhenParticipantsAvailable.setEnabled_(bool(len(self._participants)))
            self.participantsTable.reloadData()
            self.removeAllParticipants.setHidden_(False if len(self._participants) > 1 else True)
            self.participantsTable.scrollRowToVisible_(len(self._participants)-1)
            self.participant.setStringValue_('')

    @objc.IBAction
    def okClicked_(self, sender):
        if self.validateConference():
            NSApp.stopModalWithCode_(NSOKButton)

    @objc.IBAction
    def cancelClicked_(self, sender):
        self._participants = []
        self.participantsTable.reloadData()
        self.removeAllParticipants.setHidden_(True)
        NSApp.stopModalWithCode_(NSCancelButton)

    def windowShouldClose_(self, sender):
        NSApp.stopModalWithCode_(NSCancelButton)
        return True

    def selectedParticipant(self):
        try:
            row = self.participantsTable.selectedRow()
            return self._participants[row]
        except IndexError:
            return None

    def validateRoom(self, allow_random_room=True):
        if not self.room.stringValue().strip() and allow_random_room:
            room = random_room()
        else:
            room=self.room.stringValue().lower().strip()

        if not re.match("^[+1-9a-z][0-9a-z_.-]{0,65}[0-9a-z]", room):
            NSRunAlertPanel("Conference Room", "Please enter a valid conference room of at least 2 alpha-numeric . _ or - characters, it must start and end with a +, a positive digit or letter",
                "OK", None, None)
            return False
        else:
            return room

    def validateConference(self, allow_random_room=True):
        self.nickname = self.nickname_textfield.stringValue().strip()
        room = self.validateRoom(allow_random_room)
        if not room:
            return False

        if self.chat.state() == NSOffState and self.audio.state() == NSOffState:
            NSRunAlertPanel("Start a new Conference", "Please select at least one media type.",
                "OK", None, None)
            return False

        if "@" in room:
            self.target = u'%s' % room
        else:
            account = AccountManager().default_account
            if isinstance(account, BonjourAccount):
                item = self.bonjour_server_combolist.selectedItem()
                if item is None:
                    NSRunAlertPanel('Start a new Conference', 'No SylkServer in the Neighbourhood', "OK", None, None)
                    return False

                object = item.representedObject()
                if hasattr(object, 'host'):
                    self.target = u'%s@%s:%s;transport=%s' % (room, object.uri.host, object.uri.port, object.uri.parameters.get('transport','udp'))
                else:
                    NSRunAlertPanel('Start a new Conference', 'No SylkServer in the Neighbourhood', "OK", None, None)
                    return False
            else:
                if account.conference.server_address:
                    self.target = u'%s@%s' % (room, account.conference.server_address)
                else:
                    self.target = u'%s@%s' % (room, default_conference_server)

        if not validateParticipant(self.target):
            text = 'Invalid conference SIP URI: %s' % self.target
            NSRunAlertPanel("Start a new Conference", text,"OK", None, None)
            return False

        return True

class AddParticipantsWindowController(NSObject):
    window = objc.IBOutlet()
    addRemove = objc.IBOutlet()
    participant = objc.IBOutlet()
    participantsTable = objc.IBOutlet()
    target = objc.IBOutlet()
    startWhenParticipantsAvailable = objc.IBOutlet()

    def __new__(cls, *args, **kwargs):
        return cls.alloc().init()

    def __init__(self, target=None, default_domain=None):
        self._participants = []
        self.default_domain = default_domain
        NSBundle.loadNibNamed_owner_("AddParticipantsWindow", self)

        if target is not None:
            self.target.setStringValue_(target)
            self.target.setHidden_(False)

    def numberOfRowsInTableView_(self, table):
        try:
            return len(self._participants)
        except:
            return 0

    def tableView_objectValueForTableColumn_row_(self, table, column, row):
        try:
           return self._participants[row]
        except:
           return None

    def awakeFromNib(self):
        self.participantsTable.registerForDraggedTypes_(NSArray.arrayWithObjects_("x-blink-sip-uri"))

    @allocate_autorelease_pool
    def tableView_acceptDrop_row_dropOperation_(self, table, info, row, oper):
        pboard = info.draggingPasteboard()
        if pboard.availableTypeFromArray_(["x-blink-sip-uri"]):
            group, blink_contact = eval(pboard.stringForType_("dragged-contact"))
            if blink_contact is not None:
                sourceGroup = NSApp.delegate().contactsWindowController.model.groupsList[group]
                sourceContact = sourceGroup.contacts[blink_contact]

                if len(sourceContact.uris) > 1:
                    point = table.window().convertScreenToBase_(NSEvent.mouseLocation())
                    event = NSEvent.mouseEventWithType_location_modifierFlags_timestamp_windowNumber_context_eventNumber_clickCount_pressure_(
                                                                                                                                              NSLeftMouseUp, point, 0, NSDate.timeIntervalSinceReferenceDate(), table.window().windowNumber(),
                                                                                                                                              table.window().graphicsContext(), 0, 1, 0)
                    invite_menu = NSMenu.alloc().init()
                    titem = invite_menu.addItemWithTitle_action_keyEquivalent_(u'Invite To Conference', "", "")
                    titem.setEnabled_(False)
                    for uri in sourceContact.uris:
                        titem = invite_menu.addItemWithTitle_action_keyEquivalent_('%s (%s)' % (uri.uri, uri.type), "addContactUriToInvitationList:", "")
                        titem.setIndentationLevel_(1)
                        titem.setTarget_(self)
                        titem.setRepresentedObject_(uri.uri)

                    NSMenu.popUpContextMenu_withEvent_forView_(invite_menu, event, table)
                    return True
                else:
                    participant = info.draggingPasteboard().stringForType_("x-blink-sip-uri")
                    self.addContactUriToInvitationList(participant)
                    return True
        return False

    def tableView_validateDrop_proposedRow_proposedDropOperation_(self, table, info, row, oper):
        if info.draggingPasteboard().availableTypeFromArray_(["x-blink-sip-uri"]):
            participant = info.draggingPasteboard().stringForType_("x-blink-sip-uri")
            if participant:
                participant = sip_prefix_pattern.sub("", str(participant))
            if participant and "@" not in participant and self.default_domain:
                participant = '%s@%s' % (participant, self.default_domain)
            if participant is None or not validateParticipant(participant):
                return NSDragOperationNone
            return NSDragOperationGeneric
        else:
            return NSDragOperationNone

    @objc.IBAction
    def addContactUriToInvitationList_(self, sender):
        participant = sender.representedObject()
        self.addContactUriToInvitationList(participant)

    def addContactUriToInvitationList(self, participant):
        if participant and "@" not in participant and self.default_domain:
            participant = '%s@%s' % (participant, self.default_domain)

        if participant:
            participant = sip_prefix_pattern.sub("", str(participant))

        try:
            if participant not in self._participants:
                self._participants.append(participant)
                self.startWhenParticipantsAvailable.setEnabled_(True)
                self.participantsTable.reloadData()
                self.removeAllParticipants.setHidden_(False if len(self._participants) > 1 else True)
                self.participantsTable.scrollRowToVisible_(len(self._participants)-1)
        except:
            pass

    def run(self):
        self._participants = []
        contactsWindow = NSApp.delegate().contactsWindowController.window()
        worksWhenModal = contactsWindow.worksWhenModal()
        contactsWindow.setWorksWhenModal_(True)
        self.window.makeKeyAndOrderFront_(None)
        rc = NSApp.runModalForWindow_(self.window)
        self.window.orderOut_(self)
        contactsWindow.setWorksWhenModal_(worksWhenModal)

        if rc == NSOKButton:
            # make a copy of the participants and reset the table data source,
            participants = self._participants

            # Cocoa crashes if something is selected in the table view when clicking OK or Cancel button
            # reseting the data source works around this
            self._participants = []
            self.participantsTable.reloadData()
            return participants
        else:
            return None

    @objc.IBAction
    def addRemoveParticipant_(self, sender):
        if sender.selectedSegment() == 0:
            participant = self.participant.stringValue().strip().lower()

            if participant and "@" not in participant and self.default_domain:
                participant = '%s@%s' % (participant, self.default_domain)

            if participant:
                participant = sip_prefix_pattern.sub("", str(participant))

            if not participant or not validateParticipant(participant):
                NSRunAlertPanel("Add New Participant", "Participant must be a valid SIP addresses.", "OK", None, None)
                return

            if participant not in self._participants:
                self._participants.append(participant)
                self.startWhenParticipantsAvailable.setEnabled_(True)
                self.participantsTable.reloadData()
                self.participantsTable.scrollRowToVisible_(len(self._participants)-1)
                self.participant.setStringValue_('')
        elif sender.selectedSegment() == 1:
            participant = self.selectedParticipant()
            if participant is None and self._participants:
                participant = self._participants[-1]
            if participant is not None:
                self._participants.remove(participant)
                self.startWhenParticipantsAvailable.setEnabled_(bool(len(self._participants)))
                if len(self._participants) == 0:
                    self.startWhenParticipantsAvailable.setState_(NSOffState)
                self.participantsTable.reloadData()

    def addParticipant(self, participant):
        if participant and "@" not in participant:
            participant = participant + '@' + self.default_domain

        if not participant or not validateParticipant(participant):
            NSRunAlertPanel("Add New Participant", "Participant must be a valid SIP addresses.", "OK", None, None)
            return

        if participant not in self._participants:
            self.startWhenParticipantsAvailable.setEnabled_(True)
            self._participants.append(participant)
            self.participantsTable.reloadData()
            self.participantsTable.scrollRowToVisible_(len(self._participants)-1)
            self.participant.setStringValue_('')

    @objc.IBAction
    def okClicked_(self, sender):
        if not len(self._participants):
            NSRunAlertPanel("Add Participants to the Conference", "Please add at least one participant.",
                "OK", None, None)
        else:
            NSApp.stopModalWithCode_(NSOKButton)

    @objc.IBAction
    def cancelClicked_(self, sender):
        self._participants = []
        self.participantsTable.reloadData()
        NSApp.stopModalWithCode_(NSCancelButton)

    def windowShouldClose_(self, sender):
        NSApp.stopModalWithCode_(NSCancelButton)
        return True

    def selectedParticipant(self):
        row = self.participantsTable.selectedRow()
        try:
            return self._participants[row]
        except IndexError:
            return None


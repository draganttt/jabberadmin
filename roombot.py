#!/usr/bin/env python3.6
# -*- coding: utf-8 -*-

"""
A simple SleekXMPP bot to manage conf rooms.

requirements:
 python-pyasn1 0.3.7-1
 python-pyasn1-modules  0.1.5-1
"""

import time
import sys
import traceback
import sleekxmpp
import logging
import yaml
import syslog
import socket

# Python versions before 3.0 do not use UTF-8 encoding
# by default. To ensure that Unicode is handled properly
# throughout SleekXMPP, we will set the default encoding
# ourselves to UTF-8.
if sys.version_info < (3, 0):
    from sleekxmpp.util.misc_ops import setdefaultencoding
#    setdefaultencoding('utf8')
else:
    raw_input = input

configFile = "/etc/roombot.yaml"
configParams = {}

GRAPHITE_ADDR = ('127.0.0.1', int(3002))
with open(configFile, 'r') as stream:
    try:
        configParams = yaml.load(stream)
    except:
        print("Unable to read config file: %" % configFile)

SERVER      = configParams['server']
ALIAS       = configParams['alias']
JID         = configParams['jid']
PASS        = configParams['pass']
MUC         = configParams['muc']
PING_MUCS   = configParams['ping_mucs']
DOMAIN      = configParams['domain']
CONF_DOMAIN = configParams['conf_domain']
ADMINS      = configParams['admins']
HELP_MSG    = 'Usage: !make-room <roomname> [owner=user.name]'
CMDS        = { 
                 '!make-room'   : "Add a new groupchat.", 
                 '!destroy-room': "Delete a groupchat. (Admin users only)",
                 '!set-owner'   : "Set an owner for a groupchat.",
                 '!drop-owner'  : "Unset groupchat owner - lowers affiliation to member.",
                 '!set-admin'   : "Set an admin for a groupchat.",
                 '!drop-admin'  : "Unset groupchat admin - lowers affiliation to member.",
                 '!kick-alias'   : "Kick an alias from the given groupchat. (requires the user Handle/Alias not JID)",
                 '!help'        : "This msg.",
               }

class MUCBot(sleekxmpp.ClientXMPP):

    """
    A simple SleekXMPP bot that adds conference rooms.
    """

    def __init__(self, jid, password, room, nick):
        sleekxmpp.ClientXMPP.__init__(self,
                                      jid,
                                      password,
                                      plugin_config={
                                            'xep_0199': {
                                                          'keepalive': True,
                                                          'frequency': 60,
                                                          'timeout': 10}
                                      })

        self.room = room
        self.nick = nick
        self.uptime = int(time.time())

        logging.basicConfig(level=logging.DEBUG,
                    format='%(levelname)-8s %(message)s')

        self.add_event_handler("ssl_invalid_cert", self.discard)
        self.add_event_handler("socket_error", self.reconnect)
        self.add_event_handler("session_end", self.reconnect)
        self.add_event_handler("disconnected", self.reconnect)

        # The session_start event will be triggered when
        # the bot establishes its connection with the server
        # and the XML streams are ready for use. We want to
        # listen for this event so that we we can initialize
        # our roster.
        self.add_event_handler("session_start", self.start)

        # The groupchat_message event is triggered whenever a message
        # stanza is received from any chat room. If you also also
        # register a handler for the 'message' event, MUC messages
        # will be processed by both handlers.
        self.add_event_handler("groupchat_message", self.muc_message)


    def reconnect(self):
        self.uptime = int(time.time())

    def discard(self, event):
        print("!!!! Ignoring invalid SSL cert\n")
        return

    def start(self, event):
        """
        Process the session_start event.

        Typical actions for the session_start event are
        requesting the roster and broadcasting an initial
        presence stanza.

        Arguments:
            event -- An empty dictionary. The session_start
                     event does not provide any additional
                     data.
        """
        #self.get_roster()
        self.send_presence()
        self.plugin['xep_0045'].joinMUC(self.room,
                                        self.nick,
                                        maxhistory="0", #does not work with CiscoJabber.
                                        # If a room password is needed, use:
                                        # password=the_room_password,
                                        wait=True)

        #setup ping monitors for declared mucs
        for muc in PING_MUCS:
            self.scheduler.add("RTT check : %s" % muc,
                               seconds=60,
                               callback=self.rtt,
                               args=(muc,),
                               repeat=True,
                               qpointer=self.event_queue)

    # check round trip using ping.
    def rtt(self, muc):
        rtt = self.plugin['xep_0199'].ping(muc)
        print("PING %s RTT: %f" % (muc, rtt))

        try:
            ts = int(time.time())
            ns = "general.voice.minutely.jabber.%s_rtt" % muc.split("@")[0].replace(".", "_")
            metric = (ns, ts, rtt)
            self.send_metrics(metric)
        except:
            syslog.syslog("Error sending metrics: %s" % str(sys.exc_info()))
            return


    def send_metrics(self, metric_tuple):
        (metric, ts, value) = metric_tuple
        metric = u'{} {} {}\n'.format(
                    metric,
                    str(value),
                    str(ts)
        )
        
        # print("%s" % (metric))
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(GRAPHITE_ADDR)
            sock.sendall(metric.encode('utf-8'))
            sock.shutdown(socket.SHUT_WR)
            sock.close()
            #syslog.syslog("Metrics sent: %s" % metric)
        except:
            syslog.syslog("Error sending metrics: %s" % str(sys.exc_info()))

    def muc_message(self, msg):
        """
        Process incoming message stanzas from any chat room. Be aware
        that if you also have any handlers for the 'message' event,
        message stanzas may be processed by both handlers, so check
        the 'type' attribute when using a 'message' event handler.

        Whenever the bot's nickname is mentioned, respond to
        the message.

        IMPORTANT: Always check that a message is not from yourself,
                   otherwise you will create an infinite loop responding
                   to your own messages.

        This handler will reply to messages that mention
        the bot's nickname.

        Arguments:
            msg -- The received message stanza. See the documentation
                   for stanza objects and the Message stanza to see
                   how it may be used.
        """
        #this check is required so we don't run commands in history
        #returned by the jabber server.


        if ( int(time.time()) - int(self.uptime) ) > 10:

            if msg['mucnick'] != self.nick:
                try:
                    if msg['body'].startswith('!help'):
                        self.cmd_help(msg)

                    if msg['body'].startswith('!make-room'):
                        self.cmd_make_room(msg)

                    if msg['body'].startswith('!destroy-room'):
                        self.cmd_destroy_room(msg)

                    if msg['body'].startswith('!set-owner'):
                        self.cmd_set_owner(msg)

                    if msg['body'].startswith('!drop-owner'):
                        self.cmd_drop_owner(msg)

                    if msg['body'].startswith('!set-admin'):
                        self.cmd_set_admin(msg)

                    if msg['body'].startswith('!drop-admin'):
                        self.cmd_drop_admin(msg)

                    if msg['body'].startswith('!kick-alias'):
                        self.cmd_kick_user(msg)


                except:
                   err = "cmd unsuccesful, see bot logs."
                   self.send_message(mto=msg['from'].bare,
                                         mbody="%s" % err,
                                         mtype='groupchat')
                   print("Dumping Traceback =====\n")
                   print(sys.exc_info())
                   print(traceback.print_tb(sys.exc_info()[2]))

                   print("=======================\n")
                   return

    def cmd_help(self, msg):
        reply = "commands:\n"

        for c,h in CMDS.items():
            reply += "%s : %s\n" % (c, h)

        self.send_message(mto=msg['from'].bare,
                          mbody="%s" % reply,
                          mtype='groupchat')
        return

    def cmd_destroy_room(self, msg):

        userJid = self.get_jid(msg)
        if not self.authorized(userJid):
            err = "destroy-room: %s not authorized for this cmd.\n" % userJid
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return


        #validate msg length
        tokens = msg['body'].split(' ')
        if len(tokens) < 2:
            err = "destroy-room: not enough params given.\n"
            err += "Usage: !destroy-room <roomname>\n"
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return

        room  = self.muc_room(msg)
        if not self.muc_exists(room):
            reply = "Room %s doesn't exist." % room
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % reply,
                                      mtype='groupchat')
            return

        if self.plugin['xep_0045'].destroy(room):
            reply = "Room %s destroyed." % room
        else:
            reply = "Could not destroy room %s, see logs." % room


        self.send_message(mto=msg['from'].bare,
                                  mbody="%s" % reply,
                                  mtype='groupchat')

        return


    def cmd_set_owner(self, msg):

        helpmsg = "Usage: !set-owner <roomname> owner=<user.name>\n"
        helpmsg += "e.g: !set-owner room1408 john.cusack\n"
        helpmsg += "Note: if owner= is not defined, the requesting user is set to owner."

        #validate msg length
        tokens = msg['body'].split(' ')
        if len(tokens) < 2:
            err = "Set owner: not enough params given.\n"
            err += helpmsg
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return

        room  = self.muc_room(msg)
        owner = self.muc_owner(msg)

        if not room:
            err = "Not enough params."
            err += helpmsg
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return


        if not self.muc_exists(room):
            reply = "Room %s doesn't exist." % room
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % reply,
                                      mtype='groupchat')
            return

        self.make_room_persistent(room)

        self.plugin['xep_0045'].joinMUC(room, self.nick)

        ret = self.plugin['xep_0045'].setAffiliation( room,
                                                jid="%s" % (owner),
                                                affiliation='owner')
        if ret:
            reply = "%s added as owner for groupchat %s" % (owner, room)
        else:
            reply = "Unable to add %s as owner for groupchat %s, see logs." % (owner, room)

        self.send_message(mto=msg['from'].bare,
                              mbody=reply,
                              mtype='groupchat')

        self.plugin['xep_0045'].leaveMUC(room, self.nick)

        return


    def cmd_drop_owner(self, msg):

        helpmsg = "Drop owner: not enough params given.\n"
        helpmsg += "Usage: !drop-owner <roomname> owner=<user.name>\n"
        helpmsg += "e.g: !drop-owner room1408 owner=john.cusack\n"

        #validate msg length
        tokens = msg['body'].split(' ')
        if ((len(tokens) < 2) or ('owner=' not in msg['body'])):
            self.send_message(mto=msg['from'].bare,
                              mbody="%s" % helpmsg,
                              mtype='groupchat')
            return

        room  = self.muc_room(msg)
        owner = self.muc_owner(msg)

        ret = self.plugin['xep_0045'].setAffiliation(room, jid="%s" % (owner), affiliation='member')
        if ret:
            reply = "removed owner %s for groupchat %s" % (owner, room)
        else:
            reply = "Unable to remove owner %s for groupchat %s, see logs." % (owner, room)

        self.send_message(mto=msg['from'].bare,
                              mbody=reply,
                              mtype='groupchat')

        return

    def cmd_drop_admin(self, msg):

        helpmsg = "Drop admin: not enough params given.\n"
        helpmsg += "Usage: !drop-admin <roomname> admin=<user.name>\n"
        helpmsg += "e.g: !drop-admin room1408 admin=john.cusack\n"

        #validate msg length
        tokens = msg['body'].split(' ')
        if ((len(tokens) < 2) or ('admin=' not in msg['body'])):
            self.send_message(mto=msg['from'].bare,
                              mbody="%s" % helpmsg,
                              mtype='groupchat')
            return

        room  = self.muc_room(msg)
        admin = self.muc_admin(msg)

        ret = self.plugin['xep_0045'].setAffiliation(room, jid="%s" % (admin), affiliation='member')
        if ret:
            reply = "removed admin %s for groupchat %s" % (admin, room)
        else:
            reply = "Unable to remove admin %s for groupchat %s, see logs." % (admin, room)

        self.send_message(mto=msg['from'].bare,
                              mbody=reply,
                              mtype='groupchat')

        return


    def cmd_set_admin(self, msg):

        helpmsg = "Usage: !set-admin <roomname> admin=<user.name>\n"
        helpmsg += "e.g: !set-admin room1408 some.person\n"
        helpmsg += "Note: if admin= is not defined, the requesting user is set to admin."

        #validate msg length
        tokens = msg['body'].split(' ')
        if len(tokens) < 2:
            err = "Set admin: not enough params given.\n"
            err += helpmsg
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return

        room  = self.muc_room(msg)
        admin = self.muc_admin(msg)

        if not room:
            err = "Not enough params."
            err += helpmsg
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return


        if not self.muc_exists(room):
            reply = "Room %s doesn't exist." % room
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % reply,
                                      mtype='groupchat')
            return

        self.make_room_persistent(room)

        self.plugin['xep_0045'].joinMUC(room, self.nick)

        ret = self.plugin['xep_0045'].setAffiliation( room,
                                                jid="%s" % (admin),
                                                affiliation='admin')
        if ret:
            reply = "%s added as admin for groupchat %s" % (admin, room)
        else:
            reply = "Unable to add %s as admin for groupchat %s, see logs." % (admin, room)

        self.send_message(mto=msg['from'].bare,
                              mbody=reply,
                              mtype='groupchat')

        self.plugin['xep_0045'].leaveMUC(room, self.nick)

        return

    def cmd_kick_user(self, msg):

        helpmsg = "Usage: !kick-alias <roomname> alias=<alias>\n"
        helpmsg += "e.g: !kick-alias room1408 alias=foo bar\n"

        #validate msg length
        tokens = msg['body'].split(' ', 2)
        if len(tokens) < 3:
            err = "Kick alias: not enough params given.\n"
            err += helpmsg
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return

        if 'alias=' not in tokens[2]:
            err = "Kick alias: not enough params given.\n"
            err += helpmsg
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return

        room  = self.muc_room(msg)
        alias_tokens = tokens[2].split("=")

        if not room or len(alias_tokens) < 2:
            err = "alias or groupchat provided is incorrect."
            err += helpmsg
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return

        alias = alias_tokens[1]

        if room not in self.plugin['xep_0045'].getJoinedRooms():
            self.plugin['xep_0045'].joinMUC(room, self.nick)

        ret = self.plugin['xep_0045'].setRole(room,
                                                nick="%s" % (alias),
                                                role='none')
        if ret:
            reply = "%s kicked from groupchat %s" % (alias, room)
        else:
            reply = "Unable to kick %s from groupchat %s, see logs." % (alias, room)

        self.send_message(mto=msg['from'].bare,
                              mbody=reply,
                              mtype='groupchat')

        if room not in self.plugin['xep_0045'].getJoinedRooms():
            self.plugin['xep_0045'].leaveMUC(room, self.nick)

        return

    def cmd_make_room(self, msg):

        #validate msg length
        tokens = msg['body'].split(' ')
        if len(tokens) < 2:
            err = "Add groupchat: not enough params given.\n"
            err += HELP_MSG
            self.send_message(mto=msg['from'].bare,
                                      mbody="%s" % err,
                                      mtype='groupchat')
            return

        #carry out action
        self.add_muc(msg)

    def authorized(self, jid):
        if jid in ADMINS:
            return True
        else:
            return False

    def get_jid(self, msg):
        '''
        Gets the actual jid of muc message sender
        '''

        thisRoom = msg['from'].bare
        jid = self.plugin['xep_0045'].getJidProperty(thisRoom, msg['mucnick'], 'jid')

        #if theres a resource, strip it.
        if '/' in jid:
            return jid.split('/')[0]
        else:
            return jid

    def make_room_persistent(self, room):

        #2. get the room config as a data form
        print("<<<<< request room form:%s>>>>>" % room)
        roomform = self.plugin['xep_0045'].getRoomConfig(room)
        print(roomform)
        time.sleep(3)


        #3. update the form to make the room persistent
        roomform.set_values({'persistent': 1})
        print("<<<<< set room values >>>>>")

        ##4. submit the form
        self.plugin['xep_0045'].configureRoom(room, form=roomform)
        print("<<<<< submit room values >>>>>")
        time.sleep(1)

        return

    def muc_owner(self, msg):
        '''
        parse and return room owner passed as a parameter
        for e.g the msg['body'] contains,
          owner=foo.bar

         this returns foo.bar@domain.tld

        '''

        owner = ""

        for token in msg['body'].split(' '):
            if 'owner=' in token:
                owner = token.split('=')[1].strip()

        if not owner:
            owner = self.get_jid(msg)

        domainpart = '@' + DOMAIN
        if domainpart not in owner:
            owner += domainpart

        return owner

    def muc_admin(self, msg):
        '''
        parse and return room admin passed as a parameter
        for e.g the msg['body'] contains,
          admin=foo.bar

         this returns foo.bar@domain.tld
        '''

        admin = ""

        for token in msg['body'].split(' '):
            if 'admin=' in token:
                admin = token.split('=')[1].strip()

        if not admin:
            admin = self.get_jid(msg)

        domainpart = '@' + DOMAIN
        if domainpart not in admin:
            admin += domainpart

        return admin

    def muc_user(self, msg):
        '''
        parse and return user passed as a parameter
        for e.g the msg['body'] contains,
          user=foo.bar

         this returns foo.bar@domain.tld
        '''

        user = ""

        for token in msg['body'].split(' '):
            if 'user=' in token:
                user = token.split('=')[1].strip()

        if not user:
            return user

        domainpart = '@' + DOMAIN
        if domainpart not in user:
            user += domainpart

        return user



    def muc_room(self, msg, strip_hashtag=True):
        '''
        returns the muc room name str
        '''

        room = ""

        tokens = msg['body'].split(' ')
        room = tokens[1]

        if not strip_hashtag:
            if '#' in room:
                room = room.replace('#')

        domainpart = '@' + CONF_DOMAIN
        if domainpart not in room:
            room += domainpart

        return room

    def muc_exists(self, room):
        results = self['xep_0030'].get_items(jid=CONF_DOMAIN, iterator=True)

        for muc in results['disco_items']:
            if room == muc['jid']:
                return True

        return False

    def add_muc(self, msg):
        '''
        #Ejabberd :
        ('muc#roomconfig_persistentroom',
         <field xmlns="jabber:x:data"
                var="muc#roomconfig_persistentroom"
                type="boolean"
                label="Make room persistent">
                <value>0</value></field>)
    
        #Cisco Jabber :
        '''

        owner = self.muc_owner(msg)
        room  = self.muc_room(msg)

        roomexists = 0

        if self.muc_exists(room):
            roomexists = 1

        #1. join the requested muc, if it doesn't get listed in
        #   discovery, this means the room is not adhoc or peristent yet
        #   roombot does not attempt to join the room if it exists,
        #   because it could be locked - waiting to be configured. 
        if not roomexists:
            self.plugin['xep_0045'].joinMUC(room, self.nick)
            print("<<<<< Room doesn't already exist, joined room. >>>>>")
            #We sleep a bit here so the server doesn't ignore our command to set the room to persistent.
            time.sleep(5)

        self.make_room_persistent(room)
        time.sleep(1)
        if roomexists:
            self.plugin['xep_0045'].joinMUC(room, self.nick)
            print("<<<<< Room exists, joined room >>>>>")
            time.sleep(5)


        out = self.plugin['xep_0045'].setAffiliation( room,
                                                jid="%s" % (owner),
                                                affiliation='owner')
        print("<<<<< set affiliation >>>>>")
        time.sleep(1)

        self.plugin['xep_0045'].invite( room,
                                        owner,
                                        reason='Requested room created.')
       
        if not roomexists:
            reply = "%s added, owner set to %s." % (room, owner)
        else:
            reply = "%s exists, set to persistent and owner set to %s." % (room, owner)

        print("<<<<< invited owner -> %s >>>>>", owner)
        self.send_message(mto=msg['from'].bare,
                              mbody=reply,
                              mtype='groupchat')

        #5. leave muc
        self.plugin['xep_0045'].leaveMUC(room, self.nick)

        return 0
   
if __name__ == '__main__':
    # Setup the MUCBot and register plugins. Note that while plugins may
    # have interdependencies, the order in which you register them does
    # not matter.

    xmpp = MUCBot(JID, PASS, MUC, ALIAS)
    xmpp.register_plugin('xep_0030') # Service Discovery
    xmpp.register_plugin('xep_0045') # Multi-User Chat
    xmpp.register_plugin('xep_0199') # XMPP Ping

    # Connect to the XMPP server and start processing XMPP stanzas.
    if xmpp.connect((SERVER, 5222), reattempt=True):
        # If you do not have the dnspython library installed, you will need
        # to manually specify the name of the server if it does not match
        # the one in the JID. For example, to use Google Talk you would
        # need to use:
        #
        # if xmpp.connect(('talk.google.com', 5222)):
        #     ...
        print("Connecting...")
        xmpp.process(block=True)
        print("done.")
    else:
        print("Unable to connect.")

# Copyright (C) 2003-2007  Robey Pointer <robeypointer@gmail.com>
#
# This file is part of paramiko.
#
# Paramiko is free software; you can redistribute it and/or modify it under the
# terms of the GNU Lesser General Public License as published by the Free
# Software Foundation; either version 2.1 of the License, or (at your option)
# any later version.
#
# Paramiko is distrubuted in the hope that it will be useful, but WITHOUT ANY
# WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
# A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for more
# details.
#
# You should have received a copy of the GNU Lesser General Public License
# along with Paramiko; if not, write to the Free Software Foundation, Inc.,
# 59 Temple Place, Suite 330, Boston, MA  02111-1307  USA.

"""
L{AuthHandler}
"""

import threading
import weakref

# this helps freezing utils
import encodings.utf_8

from paramiko.common import *
from paramiko import util
from paramiko.message import Message
from paramiko.ssh_exception import SSHException, AuthenticationException, \
    BadAuthenticationType, PartialAuthentication
from paramiko.server import InteractiveQuery
from paramiko.pycompat import byt

class AuthHandler (object):
    """
    Internal class to handle the mechanics of authentication.
    """
    
    def __init__(self, transport):
        self.transport = weakref.proxy(transport)
        self.username = None
        self.authenticated = False
        self.auth_event = None
        self.auth_method = b''
        self.password = None
        self.private_key = None
        self.interactive_handler = None
        self.submethods = None
        # for server mode:
        self.auth_username = None
        self.auth_fail_count = 0
        
    def is_authenticated(self):
        return self.authenticated

    def get_username(self):
        if self.transport.server_mode:
            return self.auth_username
        else:
            return self.username

    def auth_none(self, username, event):
        self.transport.lock.acquire()
        try:
            self.auth_event = event
            self.auth_method = b'none'
            self.username = username
            self._request_auth()
        finally:
            self.transport.lock.release()

    def auth_publickey(self, username, key, event):
        self.transport.lock.acquire()
        try:
            self.auth_event = event
            self.auth_method = b'publickey'
            self.username = username
            self.private_key = key
            self._request_auth()
        finally:
            self.transport.lock.release()

    def auth_password(self, username, password, event):
        self.transport.lock.acquire()
        try:
            self.auth_event = event
            self.auth_method = b'password'
            self.username = username
            self.password = password
            self._request_auth()
        finally:
            self.transport.lock.release()
    
    def auth_interactive(self, username, handler, event, submethods=''):
        """
        response_list = handler(title, instructions, prompt_list)
        """
        self.transport.lock.acquire()
        try:
            self.auth_event = event
            self.auth_method = b'keyboard-interactive'
            self.username = username
            self.interactive_handler = handler
            self.submethods = submethods
            self._request_auth()
        finally:
            self.transport.lock.release()
    
    def abort(self):
        if self.auth_event is not None:
            self.auth_event.set()


    ###  internals...


    def _request_auth(self):
        m = Message()
        m.add_byte(byt(MSG_SERVICE_REQUEST))
        m.add_bytes(b'ssh-userauth')
        self.transport._send_message(m)

    def _disconnect_service_not_available(self):
        m = Message()
        m.add_byte(byt(MSG_DISCONNECT))
        m.add_int(DISCONNECT_SERVICE_NOT_AVAILABLE)
        m.add_bytes(b'Service not available')
        m.add_bytes(b'en')
        self.transport._send_message(m)
        self.transport.close()

    def _disconnect_no_more_auth(self):
        m = Message()
        m.add_byte(byt(MSG_DISCONNECT))
        m.add_int(DISCONNECT_NO_MORE_AUTH_METHODS_AVAILABLE)
        m.add_bytes(b'No more auth methods available')
        m.add_bytes(b'en')
        self.transport._send_message(m)
        self.transport.close()

    def _get_session_blob(self, key, service, username):
        m = Message()
        m.add_bytes(self.transport.session_id)
        m.add_byte(byt(MSG_USERAUTH_REQUEST))
        m.add_string(username, 'utf-8')
        m.add_bytes(service)
        m.add_bytes(b'publickey')
        m.add_boolean(1)
        m.add_bytes(key.get_name())
        m.add_bytes(key.getvalue())
        return m.getvalue()

    def wait_for_response(self, event):
        while True:
            event.wait(0.1)
            if not self.transport.is_active():
                e = self.transport.get_exception()
                if (e is None) or issubclass(e.__class__, EOFError):
                    e = AuthenticationException('Authentication failed.')
                raise e
            if event.isSet():
                break
        if not self.is_authenticated():
            e = self.transport.get_exception()
            if e is None:
                e = AuthenticationException('Authentication failed.')
            # this is horrible.  python Exception isn't yet descended from
            # object, so type(e) won't work. :(
            if issubclass(e.__class__, PartialAuthentication):
                return e.allowed_types
            raise e
        return []

    def _parse_service_request(self, m):
        service = m.get_bytes()
        if self.transport.server_mode and (service == b'ssh-userauth'):
            # accepted
            m = Message()
            m.add_byte(byt(MSG_SERVICE_ACCEPT))
            m.add_bytes(service)
            self.transport._send_message(m)
            return
        # dunno this one
        self._disconnect_service_not_available()

    def _parse_service_accept(self, m):
        service = m.get_bytes()
        if service == b'ssh-userauth':
            self.transport._log(DEBUG, 'userauth is OK')
            m = Message()
            m.add_byte(byt(MSG_USERAUTH_REQUEST))
            username = self.username
            if isinstance(self.username, str):
                username = username.encode('utf-8')
            m.add_bytes(username)
            m.add_bytes(b'ssh-connection')
            m.add_bytes(self.auth_method)
            if self.auth_method == b'password':
                m.add_boolean(False)
                password = self.password
                if isinstance(password, str):
                    password = password.encode('utf-8')
                m.add_bytes(password)
            elif self.auth_method == b'publickey':
                m.add_boolean(True)
                m.add_bytes(self.private_key.get_name())
                m.add_bytes(self.private_key.getvalue())
                blob = self._get_session_blob(self.private_key, b'ssh-connection', self.username)
                sig = self.private_key.sign_ssh_data(self.transport.randpool, blob)
                m.add_bytes(sig.getvalue())
            elif self.auth_method == b'keyboard-interactive':
                m.add_bytes(b'')
                m.add_string(self.submethods, 'utf-8')
            elif self.auth_method == b'none':
                pass
            else:
                raise SSHException('Unknown auth method "%s"' % self.auth_method)
            self.transport._send_message(m)
        else:
            self.transport._log(DEBUG, 'Service request "%s" accepted (?)' % service)

    def _send_auth_result(self, username, method, result):
        # okay, send result
        assert(isinstance(username,str))
        m = Message()
        if result == AUTH_SUCCESSFUL:
            self.transport._log(INFO, 'Auth granted (%s).' % method)
            m.add_byte(byt(MSG_USERAUTH_SUCCESS))
            self.authenticated = True
        else:
            self.transport._log(INFO, 'Auth rejected (%s).' % method)
            m.add_byte(byt(MSG_USERAUTH_FAILURE))
            auths = self.transport.server_object.get_allowed_auths(username)
            assert(isinstance(auths,bytes))
            m.add_bytes(auths)
            if result == AUTH_PARTIALLY_SUCCESSFUL:
                m.add_boolean(1)
            else:
                m.add_boolean(0)
                self.auth_fail_count += 1
        self.transport._send_message(m)
        if self.auth_fail_count >= 10:
            self._disconnect_no_more_auth()
        if result == AUTH_SUCCESSFUL:
            self.transport._auth_trigger()

    def _interactive_query(self, q):
        # make interactive query instead of response
        m = Message()
        m.add_byte(byt(MSG_USERAUTH_INFO_REQUEST))
        m.add_string(q.name, 'utf-8')
        m.add_string(q.instructions, 'utf-8')
        m.add_bytes(b'')
        m.add_int(len(q.prompts))
        for p in q.prompts:
            m.add_string(p[0], 'utf-8')
            m.add_boolean(p[1])
        self.transport._send_message(m)
 
    def _parse_userauth_request(self, m):
        if not self.transport.server_mode:
            # er, uh... what?
            m = Message()
            m.add_byte(byt(MSG_USERAUTH_FAILURE))
            m.add_bytes(b'none')
            m.add_boolean(0)
            self.transport._send_message(m)
            return
        if self.authenticated:
            # ignore
            return
        username = m.get_string('utf-8')
        service = m.get_bytes()
        method = m.get_bytes()
        self.transport._log(DEBUG, 'Auth request (type=%s) service=%s, username=%s' % (method, service, username))
        if service != b'ssh-connection':
            self._disconnect_service_not_available()
            return
        if (self.auth_username is not None) and (self.auth_username != username):
            self.transport._log(WARNING, 'Auth rejected because the client attempted to change username in mid-flight')
            self._disconnect_no_more_auth()
            return
        self.auth_username = username

        if method == b'none':
            result = self.transport.server_object.check_auth_none(username)
        elif method == b'password':
            changereq = m.get_boolean()
            password = m.get_bytes()
            try:
                password = password.decode('utf-8')
            except UnicodeError:
                # some clients/servers expect non-utf-8 passwords!
                # in this case, just return the raw byte string.
                pass
            if changereq:
                # always treated as failure, since we don't support changing passwords, but collect
                # the list of valid auth types from the callback anyway
                self.transport._log(DEBUG, 'Auth request to change passwords (rejected)')
                newpassword = m.get_bytes()
                try:
                    newpassword = newpassword.decode('utf-8', 'replace')
                except UnicodeError:
                    pass
                result = AUTH_FAILED
            else:
                result = self.transport.server_object.check_auth_password(username, password)
        elif method == b'publickey':
            sig_attached = m.get_boolean()
            keytype = m.get_bytes()
            keyblob = m.get_bytes()
            try:
                key = self.transport._key_info[keytype](Message(keyblob))
            except SSHException as e:
                self.transport._log(INFO, 'Auth rejected: public key: %s' % str(e))
                key = None
            except:
                self.transport._log(INFO, 'Auth rejected: unsupported or mangled public key')
                key = None
            if key is None:
                self._disconnect_no_more_auth()
                return
            # first check if this key is okay... if not, we can skip the verify
            result = self.transport.server_object.check_auth_publickey(username, key)
            if result != AUTH_FAILED:
                # key is okay, verify it
                if not sig_attached:
                    # client wants to know if this key is acceptable, before it
                    # signs anything...  send special "ok" message
                    m = Message()
                    m.add_byte(byt(MSG_USERAUTH_PK_OK))
                    m.add_bytes(keytype)
                    m.add_bytes(keyblob)
                    self.transport._send_message(m)
                    return
                sig = Message(m.get_bytes())
                blob = self._get_session_blob(key, service, username)
                if not key.verify_ssh_sig(blob, sig):
                    self.transport._log(INFO, 'Auth rejected: invalid signature')
                    result = AUTH_FAILED
        elif method == b'keyboard-interactive':
            lang = m.get_bytes()
            submethods = m.get_bytes().decode('utf-8')
            result = self.transport.server_object.check_auth_interactive(username, submethods)
            if isinstance(result, InteractiveQuery):
                # make interactive query instead of response
                self._interactive_query(result)
                return
        else:
            result = self.transport.server_object.check_auth_none(username)
        # okay, send result
        self._send_auth_result(username, method, result)

    def _parse_userauth_success(self, m):
        self.transport._log(INFO, 'Authentication (%s) successful!' % self.auth_method)
        self.authenticated = True
        self.transport._auth_trigger()
        if self.auth_event != None:
            self.auth_event.set()

    def _parse_userauth_failure(self, m):
        authlist = m.get_list()
        partial = m.get_boolean()
        if partial:
            self.transport._log(INFO, 'Authentication continues...')
            self.transport._log(DEBUG, 'Methods: ' + str(authlist))
            self.transport.saved_exception = PartialAuthentication(authlist)
        elif self.auth_method not in authlist:
            self.transport._log(DEBUG, 'Authentication type (%s) not permitted.' % self.auth_method)
            self.transport._log(DEBUG, 'Allowed methods: ' + str(authlist))
            self.transport.saved_exception = BadAuthenticationType('Bad authentication type', authlist)
        else:
            self.transport._log(INFO, 'Authentication (%s) failed.' % self.auth_method)
        self.authenticated = False
        self.username = None
        if self.auth_event != None:
            self.auth_event.set()

    def _parse_userauth_banner(self, m):
        banner = m.get_bytes()
        lang = m.get_bytes()
        self.transport._log(INFO, 'Auth banner: ' + banner)
        # who cares.
    
    def _parse_userauth_info_request(self, m):
        if self.auth_method != b'keyboard-interactive':
            raise SSHException('Illegal info request from server')
        title = m.get_bytes().decode('utf-8')
        instructions = m.get_bytes().decode('utf-8')
        m.get_bytes()  # lang
        prompts = m.get_int()
        prompt_list = []
        for i in range(prompts):
            prompt_list.append((m.get_bytes().decode('utf-8'), m.get_boolean()))
        response_list = self.interactive_handler(title, instructions, prompt_list)
        
        m = Message()
        m.add_byte(byt(MSG_USERAUTH_INFO_RESPONSE))
        m.add_int(len(response_list))
        for r in response_list:
            m.add_string(r, 'utf-8')
        self.transport._send_message(m)
    
    def _parse_userauth_info_response(self, m):
        if not self.transport.server_mode:
            raise SSHException('Illegal info response from server')
        n = m.get_int()
        responses = []
        for i in range(n):
            responses.append(m.get_bytes().decode('utf-8'))
        result = self.transport.server_object.check_auth_interactive_response(responses)
        if isinstance(type(result), InteractiveQuery):
            # make interactive query instead of response
            self._interactive_query(result)
            return
        self._send_auth_result(self.auth_username, b'keyboard-interactive', result)
        

    _handler_table = {
        MSG_SERVICE_REQUEST: _parse_service_request,
        MSG_SERVICE_ACCEPT: _parse_service_accept,
        MSG_USERAUTH_REQUEST: _parse_userauth_request,
        MSG_USERAUTH_SUCCESS: _parse_userauth_success,
        MSG_USERAUTH_FAILURE: _parse_userauth_failure,
        MSG_USERAUTH_BANNER: _parse_userauth_banner,
        MSG_USERAUTH_INFO_REQUEST: _parse_userauth_info_request,
        MSG_USERAUTH_INFO_RESPONSE: _parse_userauth_info_response,
    }


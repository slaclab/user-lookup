import asyncio

from typing import List, Optional, AsyncGenerator
import strawberry
from strawberry.types import Info
from strawberry.arguments import UNSET

from models import User, UserInput

import bonsai
from bonsai import LDAPClient

from os import environ
import logging

LOG = logging.getLogger(__name__)
logging.basicConfig( level=logging.DEBUG )

SOURCE_LDAP_SERVER = environ.get('SOURCE_LDAP_SERVER', 'ldaps://sdfldap001.sdf.slac.stanford.edu' )
SOURCE_LDAP_USER_BASEDN = environ.get('SOURCE_LDAP_USER_BASEDN',None)
SOURCE_LDAP_BIND_USERNAME = environ.get('SOURCE_LDAP_BIND_USERNAME',None)
SOURCE_LDAP_BIND_PASSWORD = environ.get('SOURCE_LDAP_BIND_PASSWORD',None)

SOURCE_LDAP_CLIENT = LDAPClient( SOURCE_LDAP_SERVER )
if SOURCE_LDAP_BIND_USERNAME and SOURCE_LDAP_BIND_PASSWORD:
    SOURCE_LDAP_CLIENT.set_credentials("SIMPLE", user=SOURCE_LDAP_BIND_USERNAME, password=SOURCE_LDAP_BIND_PASSWORD)
SOURCE_LDAP_DB = SOURCE_LDAP_CLIENT.connect()

logging.debug(f"connecting to {SOURCE_LDAP_SERVER} with {SOURCE_LDAP_BIND_USERNAME} {SOURCE_LDAP_BIND_PASSWORD}, using basedn {SOURCE_LDAP_USER_BASEDN}")

def map_entities_to_users( entity: List[dict], overrides: dict={ 'fullname': 'name', 'preferredemail': 'extensionAttribute5' }  ) -> User:
    #LOG.debug(f'translate {entity}')
    for e in entity:
        u = User(
            dn=e['dn'],
            username=e['uid'][0],
            fullname=e[overrides['fullname'] if 'fullname' in overrides else 'gecos'][0],
            uidnumber=e['uidNumber'][0],
            shell=e['loginShell'][0],
            eppns=e['mail'],
            preferredemail=e[overrides['preferredemail'] if 'preferredemail' in overrides else 'mail'][0],
            homedirectory=e['homeDirectory'][0] if 'homeDirectory' in e else f"/sdf/home/{e['uid'][0][0]}/{e['uid'][0]}")
        yield u 

def reduce_filter( filter ) -> dict:
    d = {}
    for k,v in filter.__dict__.items():
        if not v in ( UNSET, None ):
            d[k] = v
    return d

def user_filter( filter, keys={ 'username': 'uid', 'fullname': 'gecos' } ) -> str:
    d = reduce_filter( filter )
    array = []
    for k,v in d.items():
        if k in keys:
            this = keys[k]
            array.append( f'({this}={v})' )
            # deal with wild card fullname search
    return f"(&(objectclass=person){''.join(array)})"

@strawberry.type
class Query:
    @strawberry.field
    def users(self, info: Info, filter: UserInput ) -> List[User]:
        logging.debug(f"querying {filter}, with {user_filter(filter)}")
        e = SOURCE_LDAP_DB.search( SOURCE_LDAP_USER_BASEDN, bonsai.LDAPSearchScope.SUB, user_filter( filter ) )
        #logging.debug(f"found {e}")
        return map_entities_to_users( e )

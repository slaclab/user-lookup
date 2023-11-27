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

LDAP_SERVER = environ.get('LDAP_SERVER', 'ldaps://sdfldap001.sdf.slac.stanford.edu' )

LDAP_DB = LDAPClient( LDAP_SERVER ).connect()
LDAP_BASEDN = 'dc=sdf,dc=slac,dc=stanford,dc=edu'



def map_to_entities_to_users( entity: List[dict] ) -> User:
    LOG.debug(f'translate {entity}')
    for e in entity:
        u = User(
            dn=e['dn'],
            username=e['uid'][0],
            fullname=e['gecos'][0],
            uidnumber=e['uidNumber'][0],
            shell=e['loginShell'][0],
            eppns=e['mail'],
            preferredemail=e['mail'][0],
            homedirectory=e['homeDirectory'][0])
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
        e = LDAP_DB.search( LDAP_BASEDN, bonsai.LDAPSearchScope.SUB, user_filter( filter ) )
        return map_to_entities_to_users( e )

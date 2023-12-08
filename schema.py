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
logging.basicConfig( level=logging.INFO )

SOURCE_LDAP_SERVER = environ.get('SOURCE_LDAP_SERVER', 'ldaps://sdfldap001.sdf.slac.stanford.edu' )
SOURCE_LDAP_USER_BASEDN = environ.get('SOURCE_LDAP_USER_BASEDN',None)
SOURCE_LDAP_BIND_USERNAME = environ.get('SOURCE_LDAP_BIND_USERNAME',None)
SOURCE_LDAP_BIND_PASSWORD = environ.get('SOURCE_LDAP_BIND_PASSWORD',None)

SOURCE_LDAP_CLIENT = LDAPClient( SOURCE_LDAP_SERVER )
if SOURCE_LDAP_BIND_USERNAME and SOURCE_LDAP_BIND_PASSWORD:
    SOURCE_LDAP_CLIENT.set_credentials("SIMPLE", user=SOURCE_LDAP_BIND_USERNAME, password=SOURCE_LDAP_BIND_PASSWORD)

logging.info(f"connecting to {SOURCE_LDAP_SERVER} with {SOURCE_LDAP_BIND_USERNAME}, using basedn {SOURCE_LDAP_USER_BASEDN}")

def map_entities_to_users( entity: List[dict], overrides: dict={ 
      'dn': ['distinguishedName','dn'],
      'username': [ 'extensionAttribute11', 'uid' ], 
      'uidnumber': 'uidNumber',
      'fullname': ['displayName', 'gecos'],
      'preferredemail': ['extensionAttribute5','extensionAttribute11'], 
      'mail': [ 'mail', 'extensionAttribute12' ],
    }, pop=True ) -> User:

    def _get( e, field, pop=True, aggregate=False ):
        possible = ( field, )
        if field in overrides:
            possible = overrides[field] if isinstance( overrides[field], list ) else  ( overrides[field], )
        LOG.debug(f"_get {field}")
        # get all values for all defined attributes
        if not aggregate:
            for p in possible:
                if p in e:
                    LOG.debug(f"  found {e[p]}")
                    return e[p][0] if pop else e[p]
        else:
            ret = []
            for p in possible:
                # note that this would ignore pop
                if p in e:
                    ret += e[p]
            return ret
        LOG.debug(f"  not found")
        return None 

    for e in entity:
        LOG.debug(f'translate {e}')
        username = _get(e,'username').split('@').pop(0)
        preferredemail = _get(e,'preferredemail')
        eppns = [ preferredemail, ] + _get(e,'mail', pop=False, aggregate=True )
        u = User(
            dn=_get(e,'dn'),
            username=username,
            fullname=_get(e,'fullname'),
            uidnumber=_get(e,'uidnumber'),
            shell=_get(e,'loginShell'),
            eppns=list(set(eppns)),
            preferredemail=preferredemail,
            homedirectory=e['homeDirectory'][0] if 'homeDirectory' in e else f"/sdf/home/{username[0]}/{username}"
        )
        LOG.debug(f"created {u}")
        yield u 

def reduce_filter( filter ) -> dict:
    d = {}
    for k,v in filter.__dict__.items():
        if not v in ( UNSET, None ):
            d[k] = v
    return d

def user_filter( filter, keys={ 'username': 'uid', 'fullname': 'displayName' } ) -> str:
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
        logging.info(f"querying for {user_filter(filter)}")
        ans = None
        with SOURCE_LDAP_CLIENT.connect() as conn:
            ans = conn.search( SOURCE_LDAP_USER_BASEDN, bonsai.LDAPSearchScope.SUB, user_filter( filter ) )
        #logging.debug(f"found {ans}")
        return map_entities_to_users( ans )

import logging
import time
from os import environ

import bonsai
import requests
import strawberry
from bonsai import LDAPClient
from models import User, UserInput
from strawberry.arguments import UNSET
from strawberry.types import Info

LOG = logging.getLogger(__name__)
DEBUG = False
try:
  DEBUG = int(environ.get('DEBUG'))
  if DEBUG > 0:
    DEBUG = True
except:
  pass
logging.basicConfig( level=logging.DEBUG if DEBUG else logging.INFO )

# SOURCE_LDAP: maps to the Windows ldap instance
SOURCE_LDAP_SERVER = environ.get('SOURCE_LDAP_SERVER', 'ldaps://sdfldap001.sdf.slac.stanford.edu' )
SOURCE_LDAP_USER_BASEDN = environ.get('SOURCE_LDAP_USER_BASEDN',None)
SOURCE_LDAP_BIND_USERNAME = environ.get('SOURCE_LDAP_BIND_USERNAME',None)
SOURCE_LDAP_BIND_PASSWORD = environ.get('SOURCE_LDAP_BIND_PASSWORD',None)

# Load SDF LDAP env variables
SDF_LDAP_SERVER = environ.get('SDF_LDAP_SERVER', 'ldaps://sdfldap001.sdf.slac.stanford.edu')
SDF_LDAP_USER_BASEDN = environ.get('SDF_LDAP_USER_BASEDN')

SOURCE_LDAP_CLIENT = LDAPClient( SOURCE_LDAP_SERVER )
if SOURCE_LDAP_BIND_USERNAME and SOURCE_LDAP_BIND_PASSWORD:
    SOURCE_LDAP_CLIENT.set_credentials("SIMPLE", user=SOURCE_LDAP_BIND_USERNAME, password=SOURCE_LDAP_BIND_PASSWORD)

LOG.info(f"connecting to {SOURCE_LDAP_SERVER} with {SOURCE_LDAP_BIND_USERNAME}, using basedn {SOURCE_LDAP_USER_BASEDN}")

SDF_LDAP_CLIENT = LDAPClient( SDF_LDAP_SERVER )
LOG.info(f"connecting to {SDF_LDAP_SERVER} with anonymous bind, using basedn {SDF_LDAP_USER_BASEDN}")

LDAP_TIMEOUT = float( environ.get('LDAP_TIMEOUT', '30.0') )
LDAP_RETRIES = int( environ.get('LDAP_RETRIES', '3') )
LDAP_RETRY_BACKOFF = float( environ.get('LDAP_RETRY_BACKOFF', '0.5') )

# errors that will never succeed on a retry, so fail fast on them
LDAP_FATAL_ERRORS = (
    bonsai.AuthenticationError,
    bonsai.InvalidDN,
    bonsai.NoSuchObjectError,
)


def ldap_search( client: LDAPClient, base: str, filter_exp: str, attrlist: list[str] | None = None,
                 scope=bonsai.LDAPSearchScope.SUB, description: str='ldap search' ) -> list[dict]:
    """Run an ldap search, bounding each attempt with LDAP_TIMEOUT and retrying
    transient failures up to LDAP_RETRIES times. Raises the last error if every
    attempt fails."""
    attempts = LDAP_RETRIES + 1
    for attempt in range( 1, attempts + 1 ):
        try:
            with client.connect( timeout=LDAP_TIMEOUT ) as conn:
                return conn.search( base, scope, filter_exp, attrlist=attrlist, timeout=LDAP_TIMEOUT )
        except LDAP_FATAL_ERRORS as e:
            LOG.warning(f"{description} failed unrecoverably: {e}")
            raise
        except (bonsai.LDAPError, OSError) as e:
            if attempt == attempts:
                LOG.warning(f"{description} failed after {attempt} attempt(s): {e}")
                raise
            delay = LDAP_RETRY_BACKOFF * ( 2 ** ( attempt - 1 ) )
            LOG.warning(f"{description} failed (attempt {attempt}/{attempts}), retrying in {delay}s: {e}")
            time.sleep( delay )



# https://stackoverflow.com/questions/480214/how-do-i-remove-duplicates-from-a-list-while-preserving-order
def f7(seq):
    seen = set()
    seen_add = seen.add
    return [x for x in seq if not (x in seen or seen_add(x))]

def map_entities_to_users( entity: list[dict], overrides: dict={ 
      'dn': ['distinguishedName','dn'],
      'username': [ 'extensionAttribute11', 'uid', 'userPrincipalName' ], 
      'uidnumber': 'uidNumber',
      'fullname': ['displayName', 'gecos'],
      'preferredemail': ['extensionAttribute5','extensionAttribute11'], 
      'mail': [ 'mail', 'extensionAttribute12', 'extensionAttribute11' ],
    }, pop=True ) -> User:

    def _get( e, field, pop=True, aggregate=False ):
        possible = ( field, )
        if field in overrides:
            possible = overrides[field] if isinstance( overrides[field], list ) else  ( overrides[field], )
        LOG.debug(f"_get {field}")
        # get all values for all defined attributes
        if not aggregate:
            for p in possible:
                LOG.debug(f" checking key {p}")
                if p in e:
                    LOG.debug(f"  found {e[p]}")
                    return e[p][0] if pop else e[p]
        else:
            ret = []
            for p in possible:
                # note that this would ignore pop
                LOG.debug(f" checking key {p}")
                if p in e:
                    LOG.debug(f"  found {e[p]}")
                    ret += e[p]
            LOG.debug(f"  returning {ret}")
            return ret
        LOG.debug(f"  not found")
        return None 

    for e in entity:
        # junk
        for i in ( 'jpegPhoto', 'thumbnailPhoto' ):
          if i in e:
            del e[i]
        LOG.debug(f'translate {e}')
        # skip disabled accounts
        #disabled = []
        #if 'memberOf' in e:
        #  disabled = [ True for i in e['memberOf'] if 'CN=Disabled Accounts' in i ]
        #if True in disabled:
        #  LOG.debug("account is disabled")
        #  continue
        username = _get(e,'username').split('@').pop(0)
        eppns = _get(e,'mail', pop=False, aggregate=True )

        preferredemail = _get(e,'preferredemail')

        # hack to get the actual prefered address until the ldap has the correct data
        urawi_email = fetch_urawi_user_info( username )
        if urawi_email:
            preferredemail = urawi_email
        
        if not preferredemail == None:
          eppns.insert(0,preferredemail)

        if len(eppns) == 0:
          LOG.warn(f"no valid eppns found")
          continue

        gidNumber = fetch_gidNumber(username)
        secondary_gidNumbers = fetch_secondaryGidNumbers(username)
        eppns = f7(eppns)
        # create the user object
        u = User(
            dn=_get(e,'dn'),
            username=username,
            fullname=_get(e,'fullname'),
            uidnumber=_get(e,'uidnumber'),
            gidNumber=gidNumber,
            secondaryGidNumbers=secondary_gidNumbers,
            shell=_get(e,'loginShell'),
            eppns=eppns,
            preferredemail=eppns[0],
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

def user_filter( filter, keys={ 'username': 'uid', 'fullname': 'displayName', 'preferredemail': 'extensionAttribute5', 'eppns': [ 'mail', 'extensionAttribute12', 'extensionAttribute11' ] } ) -> str:
    d = reduce_filter( filter )
    array = []
    for k,v in d.items():
        #LOG.debug(f"building filter: {k}, {v} ({type(keys[k])})")
        if k in keys:
            if type(keys[k]) == str:
              this = keys[k]
              array.append( f'({this}={v})' )
            # assume OR for lists
            elif type(keys[k]) == list:
              orlist = []
              this_v = v
              if k in ( 'eppns', ):
                this_v = v[0]
              for i in keys[k]:
                orlist.append( f'({i}={this_v})' )
              this = f"(|{''.join( orlist )})"
              array.append( this )
            # deal with wild card fullname search
    return f"(&(objectclass=person){''.join(array)})"

def fetch_urawi_user_info( userid: str, token: str=None, url: str="https://userportal.slac.stanford.edu/apps/urawi/ws/user_info?psdAuthToken={token}&userid={userid}" ) -> str:
    if token == None:
        token = environ.get('URAWI_TOKEN')
    r = requests.get(url.format( userid=userid, token=token ), timeout=1).json()
    LOG.debug(f"urawi request for {userid}: {r}")
    if 'data' in r and 'preferredemail' in r['data']:
        LOG.debug(f"  found {r['data']['preferredemail']}")
        return r['data']['preferredemail'] 
    LOG.debug(f"  not found")
    return None


def fetch_gidNumber(username: str) -> int | None:
    """Look up gidNumber for a user from the sdf-ldap source."""
    try:
        results = ldap_search(
            SOURCE_LDAP_CLIENT,
            SOURCE_LDAP_USER_BASEDN,
            f"(uid={username})",
            attrlist=['gidNumber'],
            description=f"gidNumber lookup for {username}"
        )
        if results and 'gidNumber' in results[0]:
            return int(results[0]['gidNumber'][0])
        else:
            LOG.warning(f"No entry found for {username} in SOURCE_LDAP")
    except Exception as e:
        LOG.warning(f"Failed to fetch gidNumber for {username}: {e}")    
    return None

def fetch_secondaryGidNumbers(username: str) -> list[int] | None:
    """Fetch all gidNumbers for posixGroups where the user is a member."""
    try:
        results = ldap_search(
            SDF_LDAP_CLIENT,
            "ou=Group,dc=sdf,dc=slac,dc=stanford,dc=edu",
            f"(memberUid={username})",
            attrlist=['gidNumber'],
            description=f"group gidNumber lookup for {username}"
        )
        gidnumbers = []
        for entry in results:
            if 'gidNumber' in entry:
                try:
                    gidnumbers.append(int(entry['gidNumber'][0]))
                except Exception as e:
                    LOG.warning(f"Invalid gidNumber in group entry: {e}")
        return gidnumbers if gidnumbers else None
    except Exception as e:
        LOG.warning(f"Failed to fetch group gidNumbers for {username}: {e}")
    return None

@strawberry.type
class Query:
    @strawberry.field
    def users(self, info: Info, filter: UserInput ) -> list[User]:
        this_filter = user_filter( filter )
        LOG.info(f"querying for {this_filter}")
        ans = ldap_search( SOURCE_LDAP_CLIENT, SOURCE_LDAP_USER_BASEDN, this_filter,
                           description=f"user search {this_filter}" )
        #logging.debug(f"found {ans}")
        return map_entities_to_users( ans )

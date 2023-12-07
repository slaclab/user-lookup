import os
from typing import List, Optional, Dict, Union
import strawberry
from strawberry.types import Info
from strawberry.arguments import UNSET

import logging

LOG = logging.getLogger(__name__)


@strawberry.input
class UserInput:
    dn: Optional[str] = UNSET
    username: Optional[str] = UNSET
    fullname: Optional[str] = UNSET
    uidnumber: Optional[int] = None
    eppns: Optional[List[str]] = UNSET
    preferredemail: Optional[str] = UNSET
    shell: Optional[str] = UNSET
    homedirectory: Optional[str] = UNSET
    #sources: Optional[list] = UNSET

@strawberry.type
class User(UserInput):
    pass

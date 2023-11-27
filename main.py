from fastapi import FastAPI, Request

from os import environ
import logging
import re
import json

from functools import wraps
from typing import List, Optional
from enum import Enum

from fastapi import FastAPI, Depends, Request, WebSocket, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from strawberry.fastapi import BaseContext, GraphQLRouter
from strawberry import Schema
from strawberry.schema.config import StrawberryConfig
from strawberry.arguments import UNSET

from models import User
from schema import Query

LOG = logging.getLogger(__name__)
LOG.setLevel(logging.DEBUG)




schema = Schema(Query) #query=Query, config=StrawberryConfig(auto_camel_case=True))
graphql_app = GraphQLRouter( schema )

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GRAPHQL_PREFIX = environ.get('GRAPHQL_PREFIX','/graphql')
app.include_router(graphql_app, prefix=GRAPHQL_PREFIX)

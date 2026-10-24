"""Filesystem entrypoint loaded by the Hermes provider registry."""

from hermes_constants import get_hermes_home

from .hermes_omp_acp.provider import register

register(scope=str(get_hermes_home()))

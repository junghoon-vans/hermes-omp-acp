"""Filesystem entrypoint loaded by the Hermes provider registry."""

from .hermes_omp_acp.provider import register

register()

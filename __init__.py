"""Filesystem entrypoint loaded by the Hermes model-provider registry."""

from .hermes_omp_acp.provider import register

register()

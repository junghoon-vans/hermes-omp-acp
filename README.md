# hermes-omp-acp

Hermes plugins for integrating [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) through ACP.

## Plugins

| Directory | Plugin ID | Model provider ID | Purpose |
| --- | --- | --- | --- |
| [model-provider](model-provider/README.md) | `omp-acp-provider` | `omp-acp` | Use OMP as Hermes's model backend through native ACP. |
| [delegator](delegator/README.md) | `omp-acp-delegator` | — | Delegate coding tasks to OMP in retained worktrees and ACP sessions. |

Each plugin directory contains its own manifest, Python package metadata, implementation, documentation, and tests. Install individual directories, not the repository root.

## Install the model provider

```sh
hermes plugins install junghoon-vans/hermes-omp-acp/model-provider
hermes plugins enable omp-acp-provider
```

The repository is private; installation requires GitHub access. See the [provider guide](model-provider/README.md) for requirements, model selection, profile setup, and inherited ACP permission limitations.

## Install the delegator

```sh
hermes plugins install junghoon-vans/hermes-omp-acp/delegator
hermes plugins enable omp-acp-delegator
```

Configure the allowed repository roots and enable the `omp-acp-delegator` toolset as described in the [delegator guide](delegator/README.md). Delegation is channel-independent and does not change Hermes's model backend.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for development, testing, compatibility, and build instructions; CI builds both distributions and checks their independent Hermes interfaces.

The provider distribution is `hermes-omp-acp` (`hermes_omp_acp`); the delegator distribution is `hermes-omp-acp-delegator` (`hermes_omp_delegator`). Each module has its own dependencies and version.

## License

All plugins in this repository are covered by the root [MIT license](LICENSE).

# hermes-omp-acp

Hermes plugins for integrating [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) through ACP.

## Plugins

| Directory | Plugin ID | Model provider ID | Purpose |
| --- | --- | --- | --- |
| [model-provider](model-provider/README.md) | `omp-acp-provider` | `omp-acp` | Use OMP as Hermes's model backend through native ACP. |

Each plugin directory contains its own manifest, Python package metadata, implementation, documentation, and tests. Install individual directories, not the repository root.

## Install the model provider

```sh
hermes plugins install junghoon-vans/hermes-omp-acp/model-provider
hermes plugins enable omp-acp-provider
```

The repository is private; installation requires GitHub access. See the [provider guide](model-provider/README.md) for requirements, model selection, profile setup, and inherited ACP permission limitations.

## Development

See [provider development](model-provider/CONTRIBUTING.md) for environment setup, tests, native OMP integration, and distribution builds. Repository CI builds the provider directory and exercises its installed wheel against upstream Hermes and OMP.

The Python distribution is `hermes-omp-acp`, and its import package is `hermes_omp_acp`.

## License

All plugins in this repository are covered by the root [MIT license](LICENSE).

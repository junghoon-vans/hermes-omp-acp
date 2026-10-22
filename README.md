# hermes-omp-acp

Hermes plugins for integrating [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) through ACP.

## Plugins

| Directory | Plugin ID | Model provider ID | Purpose |
| --- | --- | --- | --- |
| [model-provider](model-provider/README.md) | `omp-acp-provider` | `omp-acp` | Use OMP as Hermes's model backend through native ACP. |

Each plugin directory contains its own manifest, Python package metadata, implementation, license, documentation, and tests. Install individual directories, not the repository root.

## Install the model provider

```sh
hermes plugins install junghoon-vans/hermes-omp-acp/model-provider
hermes plugins enable omp-acp-provider
```

The repository is private; installation requires GitHub access. See the [provider guide](model-provider/README.md) for requirements, model selection, profile setup, and inherited ACP permission limitations.

## Existing installations

Installations using the old plugin ID `omp-acp` need to switch to `omp-acp-provider` and the `model-provider` subdirectory. Follow the [provider migration instructions](model-provider/README.md#existing-installations); the model provider ID `omp-acp` and model settings are retained.

## Development

See [provider development](model-provider/CONTRIBUTING.md) for environment setup, tests, native OMP integration, and distribution builds. Repository CI builds the provider directory and exercises its installed wheel against upstream Hermes and OMP.

The plugin ID and Python entry point are `omp-acp-provider`. The model provider ID `omp-acp`, Python package `hermes_omp_acp`, model configuration, environment variables, and minimum supported versions are unchanged.

## License

[MIT](LICENSE). Each installable plugin also includes its own license file.

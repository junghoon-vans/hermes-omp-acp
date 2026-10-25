# hermes-omp-acp

Hermes model-provider plugin for integrating [Oh My Pi (OMP)](https://github.com/can1357/oh-my-pi) through ACP.

This repository supports one plugin: [`model-provider/`](model-provider/README.md), which lets Hermes use OMP as its model backend through native ACP.

## Install

```sh
hermes plugins install junghoon-vans/hermes-omp-acp/model-provider
hermes plugins enable omp-acp-provider
```

This installs the source plugin from GitHub. After the first PyPI release of `hermes-omp-acp-provider`, you can instead install the package into the same Python environment that runs Hermes:

```sh
python -m pip install hermes-omp-acp-provider
```

See the [provider guide](model-provider/README.md) for requirements, model selection, profile setup, and inherited ACP permission limitations.

## Development

See [CONTRIBUTING.md](CONTRIBUTING.md) for provider development, testing, compatibility, build, and publishing instructions.

The `model-provider/` directory builds the `hermes-omp-acp-provider` distribution (Python package `hermes_omp_acp`). Its Hermes plugin ID is `omp-acp-provider`, and its model-provider ID is `omp-acp`.

## License

The provider plugin is covered by the root [MIT license](LICENSE).

{ lib, buildPythonPackage, fetchPypi, pdm-backend, pydantic }:
buildPythonPackage rec {
  pname = "agent-client-protocol";
  version = "0.9.0";
  pyproject = true;
  src = fetchPypi {
    pname = "agent_client_protocol";
    inherit version;
    hash = "sha256:f744c48ab9af0f0b4452e5ab5498d61bcab97c26dbe7d6feec5fd36de49be30b";
  };
  build-system = [ pdm-backend ];
  dependencies = [ pydantic ];
  pythonImportsCheck = [ "acp" ];
  meta = {
    description = "Python ACP SDK at the official Hermes compatibility pin";
    homepage = "https://github.com/agentclientprotocol/python-sdk";
    license = lib.licenses.asl20;
  };
}

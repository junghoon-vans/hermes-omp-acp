{
  description = "Hermes OMP ACP provider and composable Python packages";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [ "x86_64-linux" "aarch64-linux" "x86_64-darwin" "aarch64-darwin" ];
      eachSystem = nixpkgs.lib.genAttrs systems;
      pythonOverlay = final: prev: {
        agent-client-protocol = final.callPackage ./nix/agent-client-protocol.nix { };
        hermes-omp-acp = final.callPackage ./nix/package.nix { };
      };
      pkgsFor = system:
        import nixpkgs { inherit system; overlays = [ self.overlays.default ]; };
    in {
      # Build in the host's interpreter scope, validating its sealed runtime
      # instead of adding our standalone SDK closure to extraPythonPackages.
      lib.mkHermesPlugin = { hermes }:
        ((hermes.python.pkgs.overrideScope pythonOverlay).hermes-omp-acp.override {
          hermesRuntime = hermes;
        });
      overlays.python = pythonOverlay;
      overlays.default = final: prev: {
        pythonPackagesExtensions = prev.pythonPackagesExtensions ++ [ pythonOverlay ];
      };
      packages = eachSystem (system: {
        default = (pkgsFor system).python313Packages.hermes-omp-acp;
        hermes-omp-acp = (pkgsFor system).python313Packages.hermes-omp-acp;
      });
      checks = eachSystem (system:
        let
          pkgs = pkgsFor system;
          installedCheck = pythonPackages:
            let
              python = pythonPackages.python.withPackages (ps: [ ps.hermes-omp-acp ps.packaging ]);
            in pkgs.runCommand "hermes-omp-acp-installed-${pythonPackages.python.pythonVersion}" { } ''
              cd "$TMPDIR"
              ${python}/bin/python ${./nix/check-installed.py}
              ${python}/bin/hermes-omp-acp-skills > skills-path
              test -f "$(cat skills-path)/omp-web-search/SKILL.md"
              touch "$out"
            '';
          sealedCheck = pythonPackages: import ./nix/check-sealed.nix {
            inherit pkgs pythonPackages;
            inherit (self.lib) mkHermesPlugin;
          };
        in {
          package = self.packages.${system}.default;
          installed = installedCheck pkgs.python313Packages;
          installed314 = installedCheck pkgs.python314Packages;
          sealed = sealedCheck pkgs.python313Packages;
          sealed314 = sealedCheck pkgs.python314Packages;
        });
    };
}

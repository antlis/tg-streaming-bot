{
  description = "tg-streaming-bot — stream video/audio into Telegram voice chats";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs =
    { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
      ];
      forAll = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      packages = forAll (pkgs: rec {
        tg-streaming-bot = pkgs.callPackage ./packaging/nix/package.nix { };
        with-browser = tg-streaming-bot.override { withBrowser = true; };
        full = tg-streaming-bot.override {
          withBrowser = true;
          withVaapi = true;
        };
        default = tg-streaming-bot;
      });

      overlays.default = final: _prev: {
        tg-streaming-bot = final.callPackage ./packaging/nix/package.nix { };
      };

      homeManagerModules.default = import ./packaging/nix/home-manager.nix self;

      devShells = forAll (pkgs: {
        default = pkgs.mkShell {
          packages = [
            self.packages.${pkgs.stdenv.hostPlatform.system}.default.pythonEnv
            pkgs.yt-dlp
            pkgs.ffmpeg
            pkgs.python3Packages.flake8
          ];
        };
      });

      checks = forAll (
        pkgs:
        let
          pkg = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
        in
        {
          package = pkg;
          # CI parity: byte-compile + showstopper lint, plus an import smoke test
          # of the bot's modules against the Nix-provided (wheel-built) deps.
          smoke =
            pkgs.runCommand "tg-streaming-bot-smoke"
              {
                nativeBuildInputs = [
                  pkg.pythonEnv
                  pkgs.python3Packages.flake8
                ];
              }
              ''
                cp -r ${./.}/. src_tree && chmod -R u+w src_tree && cd src_tree
                python -m compileall -q config.py main.py cache program driver
                flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
                mkdir -p $TMPDIR/work/downloads && cd $TMPDIR/work
                export HOME=$TMPDIR API_ID=1 API_HASH=x BOT_TOKEN=1:x BOT_USERNAME=x SESSION_NAME=x SUDO_USERS=1
                PYTHONPATH=$OLDPWD python -c "
                import importlib, pkgutil
                import config, cache.admins, driver, program
                n = 0
                for pkg in (driver, program):
                    for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + '.'):
                        importlib.import_module(m.name); n += 1
                import pytgcalls, pyrogram, ntgcalls, tgcrypto, youtubesearchpython
                print('imported', n, 'modules ok')"
                touch $out
              '';
        }
      );
    };
}

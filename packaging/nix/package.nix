{
  lib,
  stdenv,
  python3,
  callPackage,
  makeWrapper,
  yt-dlp,
  ffmpeg,
  git,
  playwright-driver,
  intel-media-driver,
  # Headless-Chromium fallback for JS-only players (opt-in: large closure).
  withBrowser ? false,
  # VA-API hardware transcoding (TRANSCODE_HWACCEL=vaapi) with Intel's iHD driver.
  withVaapi ? false,
}:

let
  extra = callPackage ./python-deps.nix { };
  pythonEnv = python3.withPackages (
    ps:
    [
      ps.aiofiles
      ps.aiohttp
      ps.curl-cffi
      ps.ffmpeg-python
      ps.gitpython
      ps.pillow
      ps.psutil
      ps.pycryptodomex
      ps.python-dotenv
      ps.requests
      ps.wget
      ps.youtube-search
      extra.kurigram
      extra.py-tgcalls
      extra.youtube-search-python
    ]
    ++ lib.optional withBrowser ps.playwright
  );
  runtimePath = lib.makeBinPath [
    yt-dlp
    ffmpeg
    git
  ];
in
stdenv.mkDerivation {
  pname = "tg-streaming-bot";
  # Keep in sync with the release tag / CHANGELOG.
  version = "1.11.0";

  src = lib.fileset.toSource {
    root = ../..;
    fileset = lib.fileset.unions [
      ../../main.py
      ../../config.py
      ../../cache
      ../../driver
      ../../program
    ];
  };

  nativeBuildInputs = [ makeWrapper ];
  dontBuild = true;

  # The bot keeps everything relative to its working directory: downloads/
  # (sessions, resume state, cache), search/ (thumbnails), and reads
  # driver/source/ (background + fonts). The code lives in the read-only store,
  # so the launcher cd's into a writable state dir and links the assets in.
  installPhase = ''
    runHook preInstall
    share=$out/share/tg-streaming-bot
    mkdir -p $share $out/bin
    cp -r main.py config.py cache driver program $share/

    cat > $out/bin/tg-streaming-bot <<EOF2
    #!${stdenv.shell}
    state="\''${TG_STREAMING_BOT_HOME:-\''${XDG_STATE_HOME:-\$HOME/.local/state}/tg-streaming-bot}"
    mkdir -p "\$state/downloads" "\$state/search" "\$state/driver"
    ln -sfn $share/driver/source "\$state/driver/source"
    cd "\$state"
    exec ${pythonEnv.interpreter} $share/main.py "\$@"
    EOF2
    chmod +x $out/bin/tg-streaming-bot
    wrapProgram $out/bin/tg-streaming-bot \
      --prefix PATH : ${runtimePath} ${
        lib.optionalString withBrowser "--set-default PLAYWRIGHT_BROWSERS_PATH ${playwright-driver.browsers}"
      } ${lib.optionalString withVaapi "--set-default LIBVA_DRIVERS_PATH ${intel-media-driver}/lib/dri"}
    runHook postInstall
  '';

  passthru = { inherit pythonEnv; };

  meta = {
    description = "Telegram bot that streams video/audio into group voice chats";
    homepage = "https://github.com/antlis/tg-streaming-bot";
    license = lib.licenses.mit;
    mainProgram = "tg-streaming-bot";
    platforms = [
      "x86_64-linux"
      "aarch64-linux"
    ];
  };
}

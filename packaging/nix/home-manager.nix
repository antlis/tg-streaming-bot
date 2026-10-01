self:
{ config, lib, pkgs, ... }:

let
  cfg = config.services.tg-streaming-bot;
  inherit (lib) mkEnableOption mkOption mkIf types;
in
{
  options.services.tg-streaming-bot = {
    enable = mkEnableOption "tg-streaming-bot, a Telegram voice-chat streaming bot";

    package = mkOption {
      type = types.package;
      default = self.packages.${pkgs.stdenv.hostPlatform.system}.default;
      defaultText = "tg-streaming-bot flake package";
    };

    environmentFile = mkOption {
      type = types.nullOr types.str;
      default = null;
      example = "/home/me/.config/tg-streaming-bot.env";
      description = ''
        File with API_ID, API_HASH, BOT_TOKEN, SESSION_NAME (the assistant's
        string session), SUDO_USERS, … kept out of the Nix store. Pass it as a
        string, not a Nix path literal.
      '';
    };

    settings = mkOption {
      type = types.attrsOf types.str;
      default = { };
      example = {
        TRANSCODE_HWACCEL = "vaapi";
        PLUGIN_DIR = "/home/me/tg-streaming-bot/plugins";
      };
      description = "Extra environment variables (see example.env).";
    };
  };

  config = mkIf cfg.enable {
    systemd.user.services.tg-streaming-bot = {
      Unit = {
        Description = "tg-streaming-bot — Telegram voice-chat streaming";
        After = [ "network-online.target" ];
        Wants = [ "network-online.target" ];
      };
      Service = {
        ExecStart = lib.getExe cfg.package;
        Restart = "on-failure";
        RestartSec = 5;
        # ~/.local/state/tg-streaming-bot — downloads/, bot session, resume state
        StateDirectory = "tg-streaming-bot";
        Environment = lib.mapAttrsToList (k: v: "${k}=${v}") (
          { TG_STREAMING_BOT_HOME = "%S/tg-streaming-bot"; } // cfg.settings
        );
        EnvironmentFile = mkIf (cfg.environmentFile != null) cfg.environmentFile;
      };
      Install.WantedBy = [ "default.target" ];
    };
  };
}

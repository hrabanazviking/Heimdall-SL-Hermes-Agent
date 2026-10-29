using System.Text.Json.Nodes;

namespace Heimdall.Core;

/// <summary>
/// The §7.2 command side: one JSON object in, one response object out
/// (<c>{"id", "status": "ok"|"error", "result"|"error"}</c>).
///
/// Two refusals are structural, not policy: a command carrying a
/// password is rejected (credentials reach the Core only through the
/// operator-provisioned process environment), and <c>move_to</c> is
/// refused as not-implemented rather than faked — walking automation
/// is not part of this slice; teleport is the relocation path.
/// Which commands may be *spoken* at all, the daily cap, the kill
/// switch and the reflex-tier rules are enforced on the Python side
/// by the safety governor before a command ever reaches this socket.
/// </summary>
public sealed class CommandProcessor
{
    private readonly ISlSession _session;
    private string _mode = "attended";

    public CommandProcessor(ISlSession session) => _session = session;

    public JsonObject Handle(JsonObject wire)
    {
        var id = wire["id"]?.GetValue<string>() ?? "";
        var cmd = wire["cmd"]?.GetValue<string>() ?? "";

        if (wire.ContainsKey("password"))
            return Error(id, cmd, "password_not_accepted_over_ipc");

        string? failure;
        JsonObject result;
        switch (cmd)
        {
            case "login":
                failure = _session.Login();
                result = new JsonObject { ["logged_in"] = _session.LoggedIn };
                break;
            case "logout":
                _session.Logout();
                failure = null;
                result = new JsonObject { ["logged_in"] = false };
                break;
            case "say":
            case "chat":
                failure = _session.SendChat(
                    Str(wire, "msg"), Int(wire, "channel", 0), Str(wire, "type", "normal"));
                result = new JsonObject { ["sent"] = failure == null };
                break;
            case "im":
                failure = _session.SendIM(Str(wire, "to_uuid"), Str(wire, "msg"));
                result = new JsonObject { ["sent"] = failure == null };
                break;
            case "group_im":
                failure = _session.SendGroupIM(Str(wire, "group_uuid"), Str(wire, "msg"));
                result = new JsonObject { ["sent"] = failure == null };
                break;
            case "teleport":
                failure = _session.Teleport(
                    Str(wire, "region"), Dbl(wire, "x", 128), Dbl(wire, "y", 128), Dbl(wire, "z", 25));
                result = new JsonObject { ["teleported"] = failure == null };
                break;
            case "sit":
                failure = _session.Sit(Str(wire, "target_uuid"));
                result = new JsonObject { ["sitting"] = failure == null };
                break;
            case "stand":
                failure = _session.Stand();
                result = new JsonObject { ["standing"] = failure == null };
                break;
            case "move_to":
                return Error(id, cmd, "not_implemented_in_minimal_client");
            case "ping":
            case "status":
            case "get_status":
                failure = null;
                result = _session.Status();
                result["mode"] = _mode;
                break;
            case "set_mode":
                _mode = Str(wire, "mode", _mode);
                failure = null;
                result = new JsonObject
                {
                    ["mode"] = _mode,
                    ["enforced_by"] = "bifrost_governor",
                };
                break;
            case "rlv_status":
                // No RLV engine is attached in this slice; say so
                // plainly instead of implying restrictions are held.
                failure = null;
                result = new JsonObject
                {
                    ["active"] = new JsonArray(),
                    ["engine"] = "not_in_this_slice",
                };
                break;
            default:
                return Error(id, cmd, "unknown_command");
        }

        return failure == null
            ? new JsonObject { ["id"] = id, ["cmd"] = cmd, ["status"] = "ok", ["result"] = result }
            : Error(id, cmd, failure);
    }

    private static JsonObject Error(string id, string cmd, string error) => new()
    {
        ["id"] = id,
        ["cmd"] = cmd,
        ["status"] = "error",
        ["error"] = error,
    };

    private static string Str(JsonObject o, string key, string fallback = "") =>
        o[key]?.GetValue<string>() ?? fallback;

    private static int Int(JsonObject o, string key, int fallback) =>
        o[key] is JsonValue v && v.TryGetValue<int>(out var i) ? i : fallback;

    private static double Dbl(JsonObject o, string key, double fallback) =>
        o[key] is JsonValue v && v.TryGetValue<double>(out var d) ? d : fallback;
}

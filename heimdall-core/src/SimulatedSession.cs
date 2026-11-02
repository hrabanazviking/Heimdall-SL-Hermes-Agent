using System.Text.Json.Nodes;

namespace Heimdall.Core;

/// <summary>
/// Offline fixture session — the seam's test double, in-process.
///
/// Exists so the IPC contract, the command processor and the Python
/// side can be proven end-to-end with no grid, no account and no
/// credentials (the login spike stays blocked until Secure Vault
/// credentials exist). It is reachable ONLY through the explicit
/// <c>--simulated</c> flag / <c>HEIMDALL_SIMULATED=1</c>, and every
/// event it emits carries <c>"simulated": true</c> so nothing it says
/// can be mistaken for the real world downstream.
/// </summary>
public sealed class SimulatedSession : ISlSession
{
    private bool _loggedIn;

    public event Action<JsonObject>? EventEmitted;

    public bool LoggedIn => _loggedIn;
    public bool Simulated => true;
    public string? AgentName => _loggedIn ? "Simulated Agent" : null;
    public string? AgentUuid => _loggedIn ? "00000000-0000-0000-0000-0000000000a1" : null;
    public string? RegionName => _loggedIn ? "Simulated Region" : null;

    private void Emit(string name, JsonObject payload)
    {
        payload["event"] = name;
        payload["simulated"] = true;
        EventEmitted?.Invoke(payload);
    }

    public string? Login()
    {
        if (_loggedIn) return null;
        _loggedIn = true;
        Emit("LoginSuccess", new JsonObject { ["agent_name"] = AgentName });
        Emit("RegionChanged", new JsonObject
        {
            ["region_name"] = "Simulated Region",
            ["sim"] = "Simulated Region",
        });
        return null;
    }

    public void Logout()
    {
        if (!_loggedIn) return;
        _loggedIn = false;
        Emit("Disconnected", new JsonObject { ["reason"] = "logout" });
    }

    public string? SendChat(string message, int channel, string chatType)
    {
        if (!_loggedIn) return "not_logged_in";
        // The fixture answers once, from a clearly-named fixture
        // resident, so consumers can prove the hear path end-to-end.
        Emit("ChatReceived", new JsonObject
        {
            ["from"] = "Simulated Resident",
            ["from_uuid"] = "00000000-0000-0000-0000-0000000000b1",
            ["msg"] = "Simulated reply: I heard you.",
            ["channel"] = channel,
            ["type"] = "Normal",
            ["source_pos"] = new JsonArray(128.0, 128.0, 24.0),
        });
        return null;
    }

    public string? SendIM(string toUuid, string message) => _loggedIn ? null : "not_logged_in";

    public string? SendGroupIM(string groupUuid, string message) => _loggedIn ? null : "not_logged_in";

    public string? Teleport(string region, double x, double y, double z)
    {
        if (!_loggedIn) return "not_logged_in";
        Emit("TeleportProgress", new JsonObject { ["stage"] = "Complete", ["region"] = region });
        Emit("RegionChanged", new JsonObject { ["region_name"] = region, ["sim"] = region });
        return null;
    }

    public string? Sit(string targetUuid) => _loggedIn ? null : "not_logged_in";

    public string? Stand() => _loggedIn ? null : "not_logged_in";

    public JsonObject Status() => new()
    {
        ["state"] = _loggedIn ? "logged_in" : "not_logged_in",
        ["logged_in"] = _loggedIn,
        ["simulated"] = true,
        ["core_version"] = CoreInfo.CoreVersion,
        ["dotnet_runtime"] = CoreInfo.DotNetRuntime,
        ["libremetaverse_version"] = CoreInfo.LibreMetaverseVersion,
        ["agent_name"] = AgentName,
        ["agent_uuid"] = AgentUuid,
        ["region"] = RegionName,
        ["position"] = _loggedIn ? new JsonArray(128.0, 128.0, 24.0) : null,
    };

    public void Dispose() { }
}

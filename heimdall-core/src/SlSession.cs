using System.Text.Json.Nodes;
using OpenMetaverse;

namespace Heimdall.Core;

/// <summary>
/// The real body: a headless LibreMetaverse 2.6.4 client wired onto
/// the §7.3 Core event names. Every signature used here was verified
/// by reflection against the pinned package on the build host
/// (2026-10-29) — GridClient's managers are fields, login is
/// <c>Network.LoginAsync(LoginParams)</c>, chat is
/// <c>Self.Chat(string, int, ChatType, bool)</c>, IM arrives on
/// <c>Self.IM</c>, and 2.6.4's appearance signal is
/// <c>Appearance.AppearanceSet</c> (there is no AppearanceChanged
/// event in this version; it is mapped onto the §7.3 name).
///
/// Honest scope for this slice: login/logout, local chat hear/send,
/// IM hear/send, group IM send, region change, friend online/offline,
/// appearance-set, object properties, teleport, sit/stand, a ~1 Hz
/// self-position feed, disconnect. NOT here: RLV (a later slice —
/// <c>rlv_status</c> answers honestly that no engine is attached),
/// walking automation (<c>move_to</c> is refused, never faked),
/// parcel tracking, avatar appeared/left, voice.
/// </summary>
public sealed class SlSession : ISlSession
{
    private readonly CoreConfig _config;
    private readonly GridClient _client = new();
    private readonly Timer _positionTimer;
    private Vector3 _lastPosition = Vector3.Zero;
    private bool _loggedIn;

    public event Action<JsonObject>? EventEmitted;

    public SlSession(CoreConfig config)
    {
        _config = config;
        WireEvents();
        _positionTimer = new Timer(_ => PublishPosition(), null,
            TimeSpan.FromSeconds(1), TimeSpan.FromSeconds(1));
    }

    public bool LoggedIn => _loggedIn;
    public bool Simulated => false;
    public string? AgentName => _loggedIn ? _client.Self.Name : null;
    public string? AgentUuid => _loggedIn ? _client.Network.LoginResponseData?.AgentID.ToString() : null;
    public string? RegionName => _loggedIn ? _client.Network.CurrentSim?.Name : null;

    private void Emit(string name, JsonObject payload)
    {
        payload["event"] = name;
        EventEmitted?.Invoke(payload);
    }

    private static JsonArray Pos(Vector3 v) => new((double)v.X, (double)v.Y, (double)v.Z);

    private void WireEvents()
    {
        _client.Self.ChatFromSimulator += (_, e) => Emit("ChatReceived", new JsonObject
        {
            ["from"] = e.FromName,
            ["from_uuid"] = e.SourceID.ToString(),
            ["msg"] = e.Message,
            ["channel"] = 0, // ChatEventArgs carries no channel; simulator chat is channel 0
            ["type"] = e.Type.ToString(),
            ["source_pos"] = Pos(e.Position),
        });

        _client.Self.IM += (_, e) =>
        {
            // 2.6.4 has no MessageFromGroup dialog: group IMs arrive
            // as MessageFromAgent (or a group session dialog) with
            // the GroupIM flag set — route on the flag.
            if (e.IM.Dialog != InstantMessageDialog.MessageFromAgent &&
                e.IM.Dialog != InstantMessageDialog.SessionGroupStart &&
                e.IM.Dialog != InstantMessageDialog.SessionSend)
                return; // typing notices, offers, etc. are later-slice concerns
            Emit(e.IM.GroupIM ? "GroupChatReceived" : "IMReceived", new JsonObject
            {
                ["from"] = e.IM.FromAgentName,
                ["from_uuid"] = e.IM.FromAgentID.ToString(),
                ["msg"] = e.IM.Message,
                ["timestamp"] = e.IM.Timestamp.ToString("o"),
            });
        };

        _client.Network.SimChanged += (_, _) =>
        {
            var sim = _client.Network.CurrentSim;
            if (sim != null)
                Emit("RegionChanged", new JsonObject
                {
                    ["region_name"] = sim.Name,
                    ["sim"] = sim.Name,
                });
        };

        _client.Network.Disconnected += (_, e) =>
        {
            _loggedIn = false;
            Emit("Disconnected", new JsonObject
            {
                ["reason"] = string.IsNullOrEmpty(e.Message) ? e.Reason.ToString() : e.Message,
            });
        };

        _client.Friends.FriendOnline += (_, e) => Emit("FriendOnline", new JsonObject
        {
            ["name"] = e.Friend.Name,
            ["uuid"] = e.Friend.UUID.ToString(),
        });

        _client.Friends.FriendOffline += (_, e) => Emit("FriendOffline", new JsonObject
        {
            ["name"] = e.Friend.Name,
            ["uuid"] = e.Friend.UUID.ToString(),
        });

        _client.Appearance.AppearanceSet += (_, _) => Emit("AppearanceChanged", new JsonObject
        {
            ["items_worn"] = new JsonArray(),
            ["items_removed"] = new JsonArray(),
            ["detail"] = "appearance_set (2.6.4 signal; item diff not tracked in this slice)",
        });

        _client.Objects.ObjectProperties += (_, e) => Emit("ObjectInfo", new JsonObject
        {
            ["uuid"] = e.Properties.ObjectID.ToString(),
            ["name"] = e.Properties.Name,
            ["desc"] = e.Properties.Description,
            ["owner"] = e.Properties.OwnerID.ToString(),
        });

        _client.Self.TeleportProgress += (_, e) => Emit("TeleportProgress", new JsonObject
        {
            ["stage"] = e.Status.ToString(),
            ["region"] = e.Message,
        });
    }

    private void PublishPosition()
    {
        if (!_loggedIn) return;
        var pos = _client.Self.SimPosition;
        if (Vector3.Distance(pos, _lastPosition) < 0.1f) return;
        _lastPosition = pos;
        Emit("SelfPositionUpdated", new JsonObject
        {
            ["pos"] = Pos(pos),
            ["region"] = RegionName,
        });
    }

    public string? Login()
    {
        if (_loggedIn) return null;
        if (!_config.HasCredentials) return "credentials_unavailable";
        var parms = new LoginParams(_client, _config.FirstName!, _config.LastName!,
            _config.Password!, "Heimdall", "0.1.0", _config.LoginUri)
        {
            Start = _config.StartLocation,
        };
        // 2.6.4's synchronous Login wrapper is obsolete (CS0618);
        // the async-first API is the corpus-documented road (§5 of
        // the LibreMetaverse deep dive), driven synchronously here
        // because the seam's command loop is synchronous, with a
        // bounded 30 s token so a hung login cannot wedge the Core.
        bool loggedIn;
        using (var loginTimeout = new CancellationTokenSource(TimeSpan.FromSeconds(30)))
        {
            try
            {
                loggedIn = _client.Network.LoginAsync(parms, loginTimeout.Token)
                    .GetAwaiter().GetResult();
            }
            catch (Exception ex)
            {
                Emit("LoginFailed", new JsonObject { ["reason"] = ex.Message });
                return "login_failed: " + ex.Message;
            }
        }
        if (!loggedIn)
        {
            var why = _client.Network.LoginMessage;
            Emit("LoginFailed", new JsonObject { ["reason"] = why });
            return "login_failed: " + why;
        }
        _loggedIn = true;
        _lastPosition = _client.Self.SimPosition;
        Emit("LoginSuccess", new JsonObject { ["agent_name"] = AgentName });
        var sim = _client.Network.CurrentSim;
        if (sim != null)
            Emit("RegionChanged", new JsonObject
            {
                ["region_name"] = sim.Name,
                ["sim"] = sim.Name,
            });
        return null;
    }

    public void Logout()
    {
        if (!_loggedIn) return;
        _loggedIn = false;
        _client.Network.Logout();
    }

    public string? SendChat(string message, int channel, string chatType)
    {
        if (!_loggedIn) return "not_logged_in";
        var type = chatType.ToLowerInvariant() switch
        {
            "whisper" => ChatType.Whisper,
            "shout" => ChatType.Shout,
            _ => ChatType.Normal,
        };
        _client.Self.Chat(message, channel, type, true);
        return null;
    }

    public string? SendIM(string toUuid, string message)
    {
        if (!_loggedIn) return "not_logged_in";
        if (!TryUuid(toUuid, out var uuid)) return "invalid_uuid";
        _client.Self.InstantMessage(uuid, message);
        return null;
    }

    public string? SendGroupIM(string groupUuid, string message)
    {
        if (!_loggedIn) return "not_logged_in";
        if (!TryUuid(groupUuid, out var uuid)) return "invalid_uuid";
        _client.Self.InstantMessageGroup(uuid, message);
        return null;
    }

    public string? Teleport(string region, double x, double y, double z)
    {
        if (!_loggedIn) return "not_logged_in";
        var ok = _client.Self.Teleport(region,
            new Vector3((float)x, (float)y, (float)z));
        return ok ? null : "teleport_failed: " + _client.Self.TeleportMessage;
    }

    public string? Sit(string targetUuid)
    {
        if (!_loggedIn) return "not_logged_in";
        if (!TryUuid(targetUuid, out var uuid)) return "invalid_uuid";
        _client.Self.RequestSit(uuid, Vector3.Zero);
        return null;
    }

    public string? Stand()
    {
        if (!_loggedIn) return "not_logged_in";
        _client.Self.Stand();
        return null;
    }

    public JsonObject Status() => new()
    {
        ["state"] = _loggedIn ? "logged_in" : "not_logged_in",
        ["logged_in"] = _loggedIn,
        ["simulated"] = false,
        ["agent_name"] = AgentName,
        ["agent_uuid"] = AgentUuid,
        ["region"] = RegionName,
        ["position"] = _loggedIn ? Pos(_client.Self.SimPosition) : null,
        ["core_version"] = CoreInfo.CoreVersion,
        ["dotnet_runtime"] = CoreInfo.DotNetRuntime,
        ["libremetaverse_version"] = CoreInfo.LibreMetaverseVersion,
    };

    private static bool TryUuid(string text, out UUID uuid)
    {
        try { uuid = new UUID(text); return true; }
        catch { uuid = UUID.Zero; return false; }
    }

    public void Dispose()
    {
        _positionTimer.Dispose();
        if (_loggedIn) Logout();
        _client.Dispose();
    }
}

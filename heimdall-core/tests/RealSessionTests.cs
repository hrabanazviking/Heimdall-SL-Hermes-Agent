using System.Text.Json.Nodes;
using Heimdall.Core;
using Xunit;

namespace Heimdall.Core.Tests;

/// <summary>
/// The REAL session, offline: no env credentials, no grid contact.
/// Status must report not_logged_in with real version facts, login
/// must fail closed WITHOUT any network login attempt (this test
/// passing at all, with no credentials configured, is the proof —
/// the login path returns before touching the network), chat-say
/// must return the contract error, and unknown commands fail closed.
/// The grid login/stand/hear/send exit test stays BLOCKED (Phase 1,
/// Secure Vault credentials unavailable) and is NOT exercised here.
/// </summary>
public sealed class RealSessionTests : IDisposable
{
    private readonly SlSession _session;
    private readonly CommandProcessor _processor;

    public RealSessionTests()
    {
        var config = new CoreConfig(
            CommandSocket: "/tmp/unused-cmd.sock",
            EventSocket: "/tmp/unused-cmd.sock.events",
            LoginUri: "https://login.agni.lindenlab.com/cgi-bin/login.cgi",
            FirstName: null, LastName: null, Password: null,
            StartLocation: "last",
            Simulated: false);
        _session = new SlSession(config);
        _processor = new CommandProcessor(_session);
    }

    public void Dispose() => _session.Dispose();

    private JsonObject Handle(string cmd, params (string, JsonNode?)[] extra)
    {
        var wire = new JsonObject { ["id"] = "t", ["cmd"] = cmd };
        foreach (var (k, v) in extra) wire[k] = v;
        return _processor.Handle(wire);
    }

    [Fact]
    public void StatusReportsNotLoggedInWithRealVersions()
    {
        foreach (var name in new[] { "status", "get_status", "ping" })
        {
            var reply = Handle(name);
            Assert.Equal("ok", reply["status"]!.GetValue<string>());
            var result = reply["result"]!;
            Assert.Equal("not_logged_in", result["state"]!.GetValue<string>());
            Assert.False(result["logged_in"]!.GetValue<bool>());
            Assert.False(result["simulated"]!.GetValue<bool>());
            Assert.False(string.IsNullOrEmpty(result["dotnet_runtime"]!.GetValue<string>()));
            Assert.False(string.IsNullOrEmpty(result["libremetaverse_version"]!.GetValue<string>()));
        }
    }

    [Fact]
    public void LoginWithoutEnvCredentialsFailsClosed()
    {
        var reply = Handle("login");
        Assert.Equal("error", reply["status"]!.GetValue<string>());
        Assert.Equal("credentials_unavailable", reply["error"]!.GetValue<string>());
        Assert.False(_session.LoggedIn);
    }

    [Fact]
    public void SayBeforeLoginReturnsNotLoggedIn()
    {
        foreach (var name in new[] { "say", "chat" })
        {
            var reply = Handle(name, ("msg", "hello"), ("channel", 0));
            Assert.Equal("error", reply["status"]!.GetValue<string>());
            Assert.Equal("not_logged_in", reply["error"]!.GetValue<string>());
        }
    }

    [Fact]
    public void UnknownCommandFailsClosed()
    {
        var reply = Handle("definitely_not_a_command");
        Assert.Equal("error", reply["status"]!.GetValue<string>());
        Assert.Equal("unknown_command", reply["error"]!.GetValue<string>());
    }

    [Fact]
    public void LogoutWhenNotLoggedInIsOkAndStaysLoggedOut()
    {
        var reply = Handle("logout");
        Assert.Equal("ok", reply["status"]!.GetValue<string>());
        Assert.False(_session.LoggedIn);
    }
}

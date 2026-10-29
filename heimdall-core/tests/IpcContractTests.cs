using System.Net.Sockets;
using System.Text;
using System.Text.Json.Nodes;
using Heimdall.Core;
using Xunit;

namespace Heimdall.Core.Tests;

/// <summary>
/// The §7.2 IPC contract proven over real Unix sockets against the
/// simulated session — the mirror image of the Python side's FakeCore
/// tests, so both halves of the seam are pinned to the same wire.
/// </summary>
public sealed class IpcContractTests : IDisposable
{
    private readonly string _cmdPath;
    private readonly string _evtPath;
    private readonly IpcServer _server;

    public IpcContractTests()
    {
        var basePath = Path.Combine(Path.GetTempPath(), "hcore-" + Guid.NewGuid().ToString("N")[..8]);
        _cmdPath = basePath + ".sock";
        _evtPath = basePath + ".sock.events";
        var session = new SimulatedSession();
        _server = new IpcServer(_cmdPath, _evtPath, new CommandProcessor(session));
        // The same wiring Program.cs performs: session events are
        // broadcast onto the event socket.
        session.EventEmitted += _server.Broadcast;
        _server.Start();
    }

    public void Dispose() => _server.Dispose();

    private JsonObject SendCommand(JsonObject command)
    {
        using var socket = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        socket.Connect(new UnixDomainSocketEndPoint(_cmdPath));
        socket.ReceiveTimeout = 10000;
        using var stream = new NetworkStream(socket, ownsSocket: true);
        using var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
        using var reader = new StreamReader(stream, Encoding.UTF8);
        writer.WriteLine(command.ToJsonString());
        var reply = reader.ReadLine();
        Assert.NotNull(reply);
        return JsonNode.Parse(reply!)!.AsObject();
    }

    private StreamReader OpenEventStream()
    {
        var socket = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        socket.Connect(new UnixDomainSocketEndPoint(_evtPath));
        // A missed event must fail the test, never hang it.
        socket.ReceiveTimeout = 10000;
        var reader = new StreamReader(new NetworkStream(socket, ownsSocket: true), Encoding.UTF8);
        // Let the server's accept loop register this reader before
        // anything is broadcast — registration is asynchronous.
        Thread.Sleep(300);
        return reader;
    }

    [Fact]
    public void LoginThenChatRoundTripsAndEventsFlow()
    {
        using var events = OpenEventStream();

        var login = SendCommand(new JsonObject { ["id"] = "1", ["cmd"] = "login" });
        Assert.Equal("ok", login["status"]!.GetValue<string>());

        // LoginSuccess + RegionChanged were broadcast on the event socket.
        var first = JsonNode.Parse(events.ReadLine()!)!.AsObject();
        Assert.Equal("LoginSuccess", first["event"]!.GetValue<string>());
        Assert.True(first["simulated"]!.GetValue<bool>());

        var chat = SendCommand(new JsonObject
        {
            ["id"] = "2", ["cmd"] = "chat", ["msg"] = "Hello!", ["channel"] = 0,
        });
        Assert.Equal("ok", chat["status"]!.GetValue<string>());
        Assert.True(chat["result"]!["sent"]!.GetValue<bool>());

        // Drain RegionChanged, then the simulated hear-back arrives.
        string? heard = null;
        for (var i = 0; i < 3 && heard == null; i++)
        {
            var line = JsonNode.Parse(events.ReadLine()!)!.AsObject();
            if (line["event"]!.GetValue<string>() == "ChatReceived")
                heard = line["msg"]!.GetValue<string>();
        }
        Assert.Equal("Simulated reply: I heard you.", heard);
    }

    [Fact]
    public void StatusReportsTheSimulatedSession()
    {
        SendCommand(new JsonObject { ["id"] = "1", ["cmd"] = "login" });
        var status = SendCommand(new JsonObject { ["id"] = "2", ["cmd"] = "get_status" });
        Assert.Equal("ok", status["status"]!.GetValue<string>());
        Assert.True(status["result"]!["logged_in"]!.GetValue<bool>());
        Assert.True(status["result"]!["simulated"]!.GetValue<bool>());
        Assert.Equal("Simulated Region", status["result"]!["region"]!.GetValue<string>());
    }

    [Fact]
    public void ChatBeforeLoginIsAnErrorNotASilence()
    {
        var chat = SendCommand(new JsonObject { ["id"] = "1", ["cmd"] = "chat", ["msg"] = "hi" });
        Assert.Equal("error", chat["status"]!.GetValue<string>());
        Assert.Equal("not_logged_in", chat["error"]!.GetValue<string>());
    }

    [Fact]
    public void PasswordOverIpcIsRefused()
    {
        var reply = SendCommand(new JsonObject
        {
            ["id"] = "1", ["cmd"] = "login", ["password"] = "never-this-way",
        });
        Assert.Equal("error", reply["status"]!.GetValue<string>());
        Assert.Equal("password_not_accepted_over_ipc", reply["error"]!.GetValue<string>());
    }

    [Fact]
    public void MoveToIsHonestlyNotImplemented()
    {
        var reply = SendCommand(new JsonObject { ["id"] = "1", ["cmd"] = "move_to" });
        Assert.Equal("error", reply["status"]!.GetValue<string>());
        Assert.Equal("not_implemented_in_minimal_client", reply["error"]!.GetValue<string>());
    }

    [Fact]
    public void RlvStatusDoesNotPretendAnEngine()
    {
        var reply = SendCommand(new JsonObject { ["id"] = "1", ["cmd"] = "rlv_status" });
        Assert.Equal("ok", reply["status"]!.GetValue<string>());
        Assert.Equal("not_in_this_slice", reply["result"]!["engine"]!.GetValue<string>());
    }

    [Fact]
    public void UnknownCommandIsAnError()
    {
        var reply = SendCommand(new JsonObject { ["id"] = "1", ["cmd"] = "dance" });
        Assert.Equal("error", reply["status"]!.GetValue<string>());
        Assert.Equal("unknown_command", reply["error"]!.GetValue<string>());
    }
}

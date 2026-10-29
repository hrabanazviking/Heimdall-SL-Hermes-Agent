using System.Net.Sockets;
using System.Text;
using System.Text.Json.Nodes;

namespace Heimdall.Core;

/// <summary>
/// The §7.2 NDJSON transport over two Unix domain sockets:
///
/// * the command socket — the Python side connects per command, sends
///   one JSON line, reads one JSON response line;
/// * the event socket — the Python side connects once and reads one
///   §7.3 event per line for as long as it stays connected.
///
/// Stale socket files from a previous run are removed before binding,
/// so a crashed Core never wedges the seam shut.
/// </summary>
public sealed class IpcServer : IDisposable
{
    private readonly string _commandPath;
    private readonly string _eventPath;
    private readonly CommandProcessor _processor;
    private readonly CancellationTokenSource _stop = new();
    private readonly List<Stream> _eventClients = new();
    private readonly object _eventLock = new();
    private Socket? _commandListener;
    private Socket? _eventListener;

    public IpcServer(string commandPath, string eventPath, CommandProcessor processor)
    {
        _commandPath = commandPath;
        _eventPath = eventPath;
        _processor = processor;
    }

    public void Start()
    {
        _commandListener = Listen(_commandPath);
        _eventListener = Listen(_eventPath);
        _ = Task.Run(() => AcceptLoop(_commandListener, HandleCommandClient));
        _ = Task.Run(() => AcceptLoop(_eventListener, RegisterEventClient));
    }

    private static Socket Listen(string path)
    {
        if (File.Exists(path)) File.Delete(path);
        var socket = new Socket(AddressFamily.Unix, SocketType.Stream, ProtocolType.Unspecified);
        socket.Bind(new UnixDomainSocketEndPoint(path));
        socket.Listen(16);
        return socket;
    }

    private async Task AcceptLoop(Socket listener, Action<Socket> onClient)
    {
        while (!_stop.IsCancellationRequested)
        {
            Socket client;
            try { client = await listener.AcceptAsync(_stop.Token); }
            catch (OperationCanceledException) { return; }
            catch (SocketException) { return; }
            onClient(client);
        }
    }

    private void HandleCommandClient(Socket socket)
    {
        _ = Task.Run(() =>
        {
            using var stream = new NetworkStream(socket, ownsSocket: true);
            using var reader = new StreamReader(stream, Encoding.UTF8);
            using var writer = new StreamWriter(stream, new UTF8Encoding(false)) { AutoFlush = true };
            var line = reader.ReadLine();
            if (line == null) return;
            JsonObject reply;
            try
            {
                var wire = JsonNode.Parse(line) as JsonObject ?? new JsonObject();
                reply = _processor.Handle(wire);
            }
            catch (Exception ex)
            {
                reply = new JsonObject
                {
                    ["id"] = "",
                    ["status"] = "error",
                    ["error"] = "bad_request: " + ex.Message,
                };
            }
            writer.WriteLine(reply.ToJsonString());
        });
    }

    private void RegisterEventClient(Socket socket)
    {
        var stream = new NetworkStream(socket, ownsSocket: true);
        lock (_eventLock) _eventClients.Add(stream);
    }

    /// <summary>Push one event to every connected event reader.</summary>
    public void Broadcast(JsonObject evt)
    {
        var line = Encoding.UTF8.GetBytes(evt.ToJsonString() + "\n");
        lock (_eventLock)
        {
            for (var i = _eventClients.Count - 1; i >= 0; i--)
            {
                try { _eventClients[i].Write(line, 0, line.Length); }
                catch
                {
                    _eventClients[i].Dispose();
                    _eventClients.RemoveAt(i);
                }
            }
        }
    }

    public void Dispose()
    {
        _stop.Cancel();
        _commandListener?.Dispose();
        _eventListener?.Dispose();
        lock (_eventLock)
        {
            foreach (var client in _eventClients) client.Dispose();
            _eventClients.Clear();
        }
        if (File.Exists(_commandPath)) File.Delete(_commandPath);
        if (File.Exists(_eventPath)) File.Delete(_eventPath);
    }
}

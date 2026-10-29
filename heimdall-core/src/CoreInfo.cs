using System.Runtime.InteropServices;
using OpenMetaverse;

namespace Heimdall.Core;

/// <summary>Version facts reported verbatim by the status command —
/// real values read from the running process and the loaded
/// LibreMetaverse assembly, never invented strings.</summary>
public static class CoreInfo
{
    public const string CoreVersion = "0.1.0";
    public static string DotNetRuntime => RuntimeInformation.FrameworkDescription;
    public static string LibreMetaverseVersion =>
        typeof(GridClient).Assembly.GetName().Version?.ToString() ?? "unknown";
}

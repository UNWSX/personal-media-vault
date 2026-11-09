using System;
using System.Diagnostics;
using System.IO;
using System.Net.Http;
using System.Text;
using System.Text.Json;
using System.Threading;
using System.Windows.Forms;

namespace AppHost
{
    internal static class Program
    {
        private static readonly string AppDir = AppDomain.CurrentDomain.BaseDirectory;
        private static readonly string? ExpectedVaultRoot = ResolveVaultRoot();
        internal static Process? ServerProcess;
        private static int ServerPort = PortStart;
        private const string AppId = "media-vault";
        private const string ExpectedVersion = "2.5.19";
        private const int ExpectedProtocolVersion = 1;
        private const int PortStart = 8765;
        private const int PortEnd = 8775;

        [STAThread]
        private static void Main()
        {
            Application.SetHighDpiMode(HighDpiMode.PerMonitorV2);
            Application.EnableVisualStyles();
            Application.SetCompatibleTextRenderingDefault(false);

            int port = FindRunning(ExpectedVaultRoot);
            if (port == 0)
            {
                port = StartServer(ExpectedVaultRoot);
                if (port == 0)
                {
                    MessageBox.Show(
                        "影音库服务启动失败。\n\n请确认「影音库服务.exe」和本程序在同一个文件夹里。",
                        "影音库", MessageBoxButtons.OK, MessageBoxIcon.Error);
                    return;
                }
            }
            ServerPort = port;

            Application.Run(new MainForm(port));
        }

        private static int FindRunning(string? expectedVaultRoot)
        {
            var handler = new HttpClientHandler { UseProxy = false, UseCookies = false };
            using (var c = new HttpClient(handler) { Timeout = TimeSpan.FromMilliseconds(400) })
            {
                for (int p = PortStart; p < PortEnd; p++)
                {
                    try
                    {
                        var s = c.GetStringAsync("http://127.0.0.1:" + p + "/api/ping").GetAwaiter().GetResult();
                        using var doc = JsonDocument.Parse(s);
                        var root = doc.RootElement;
                        if (root.TryGetProperty("ok", out var ok) && ok.ValueKind == JsonValueKind.True
                            && root.TryGetProperty("app", out var app)
                            && app.GetString() == AppId
                            && root.TryGetProperty("version", out var version)
                            && version.GetString() == ExpectedVersion
                            && root.TryGetProperty("protocolVersion", out var protocol)
                            && protocol.GetInt32() == ExpectedProtocolVersion
                            && root.TryGetProperty("instanceId", out var instanceId)
                            && Guid.TryParseExact(instanceId.GetString(), "N", out _)
                            && VaultMatches(root, expectedVaultRoot))
                        {
                            return p;
                        }
                    }
                    catch { }
                }
            }
            return 0;
        }

        private static string? ResolveVaultRoot()
        {
            string? configured = Environment.GetEnvironmentVariable("MEDIAVAULT_VAULT");
            var configPath = Path.Combine(AppDir, "vault-path.txt");
            if (string.IsNullOrWhiteSpace(configured) && File.Exists(configPath))
            {
                try { configured = File.ReadLines(configPath).FirstOrDefault(); } catch { }
            }

            if (string.IsNullOrWhiteSpace(configured))
            {
                // BaseDirectory normally ends with a separator. Trim it first:
                // Directory.GetParent(".media-app\") returns ".media-app" itself.
                var appDir = AppDir.TrimEnd(
                    Path.DirectorySeparatorChar,
                    Path.AltDirectorySeparatorChar
                );
                var defaultVault = Directory.GetParent(appDir)?.FullName;
                if (!string.IsNullOrEmpty(defaultVault)
                    && Directory.Exists(Path.Combine(defaultVault, ".obsidian")))
                {
                    configured = defaultVault;
                }
                else
                {
                    // Standalone editions keep user media beside the top-level
                    // launcher. The executable itself may live in a 程序 subfolder.
                    configured = ResolvePortableDataDir();
                }
            }

            if (string.IsNullOrWhiteSpace(configured)) return null;
            try
            {
                var full = Path.GetFullPath(configured.Trim().Trim('"'));
                var portableRoot = ResolvePortableDataDir();
                return Directory.Exists(Path.Combine(full, ".obsidian"))
                    || string.Equals(
                        full.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                        portableRoot.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                        StringComparison.OrdinalIgnoreCase)
                    ? full
                    : null;
            }
            catch
            {
                return null;
            }
        }

        private static string ResolvePortableDataDir()
        {
            var appDir = AppDir.TrimEnd(
                Path.DirectorySeparatorChar,
                Path.AltDirectorySeparatorChar
            );
            var parent = Directory.GetParent(appDir)?.FullName;
            if (!string.IsNullOrEmpty(parent)
                && string.Equals(
                    Path.GetFileName(appDir),
                    "程序",
                    StringComparison.OrdinalIgnoreCase
                ))
            {
                var siblingData = Path.Combine(parent, "数据");
                if (Directory.Exists(siblingData))
                {
                    return Path.GetFullPath(siblingData);
                }
            }

            return Path.GetFullPath(Path.Combine(AppDir, "数据"));
        }

        private static bool VaultMatches(JsonElement root, string? expectedVaultRoot)
        {
            if (string.IsNullOrWhiteSpace(expectedVaultRoot)) return true;
            if (!root.TryGetProperty("vaultRoot", out var vaultRoot)) return false;
            try
            {
                var actual = Path.GetFullPath(vaultRoot.GetString() ?? "");
                return string.Equals(
                    actual.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                    expectedVaultRoot.TrimEnd(Path.DirectorySeparatorChar, Path.AltDirectorySeparatorChar),
                    StringComparison.OrdinalIgnoreCase);
            }
            catch
            {
                return false;
            }
        }

        private static int StartServer(string? expectedVaultRoot)
        {
            var exe = Path.Combine(AppDir, "影音库服务.exe");
            if (!File.Exists(exe)) return 0;

            var psi = new ProcessStartInfo(exe)
            {
                Arguments = "--port " + PortStart + " --no-browser"
                    + (string.IsNullOrWhiteSpace(expectedVaultRoot)
                        ? ""
                        : " --vault \"" + expectedVaultRoot.Replace("\"", "") + "\""),
                WorkingDirectory = AppDir,
                UseShellExecute = false,
                CreateNoWindow = true,
            };
            try { ServerProcess = Process.Start(psi); } catch { return 0; }

            var deadline = DateTime.UtcNow.AddSeconds(40);
            while (DateTime.UtcNow < deadline)
            {
                Thread.Sleep(250);
                int p = FindRunning(expectedVaultRoot);
                if (p != 0) return p;
            }
            return 0;
        }

        internal static void StopServer()
        {
            var process = ServerProcess;

            try
            {
                var handler = new HttpClientHandler { UseProxy = false, UseCookies = false };
                using var client = new HttpClient(handler) { Timeout = TimeSpan.FromSeconds(2) };
                client.DefaultRequestHeaders.Add("X-MediaVault-Client", AppId);
                using var body = new StringContent("{}", Encoding.UTF8, "application/json");
                client.PostAsync("http://127.0.0.1:" + ServerPort + "/api/quit", body)
                    .GetAwaiter().GetResult();
            }
            catch { }

            if (process == null || process.HasExited) return;
            if (process.WaitForExit(5000)) return;
            try
            {
                // 正常退出超时后，只结束本程序自己启动的服务进程树。
                Process.Start(new ProcessStartInfo("taskkill", "/PID " + process.Id + " /T /F")
                {
                    CreateNoWindow = true,
                    UseShellExecute = false,
                });
            }
            catch { }
        }
    }
}

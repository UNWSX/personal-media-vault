using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Net.Sockets;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace MediaVaultWebLauncher
{
    internal static class Program
    {
        private const string AppId = "media-vault";
        private const string ExpectedVersion = "2.5.18";
        private const int ExpectedProtocolVersion = 1;
        private const int PortStart = 8765;
        private const int PortEnd = 8775;

        [STAThread]
        private static void Main()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string appDir = Path.Combine(baseDir, "程序");
            string service = Path.Combine(appDir, "影音库服务.exe");
            string dataDir = Path.GetFullPath(Path.Combine(baseDir, "数据"));

            if (!File.Exists(service))
            {
                MessageBox.Show(
                    "没有找到“程序”目录中的影音库服务.exe。\n\n请重新解压完整安装包。",
                    "影音库网页版",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return;
            }

            int port = FindRunning(dataDir);
            Process serviceProcess = null;
            if (port == 0)
            {
                port = FindAvailablePort();
                if (port == 0)
                {
                    MessageBox.Show(
                        "本地服务端口 8765–8774 均被占用，无法启动网页版。",
                        "影音库网页版",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return;
                }

                try
                {
                    serviceProcess = Process.Start(new ProcessStartInfo(service)
                    {
                        Arguments = "--port " + port + " --no-browser --web-mode"
                            + " --vault \"" + dataDir.Replace("\"", "") + "\"",
                        WorkingDirectory = appDir,
                        UseShellExecute = false,
                        CreateNoWindow = true,
                    });
                }
                catch (Exception ex)
                {
                    MessageBox.Show(
                        "影音库网页版启动失败。\n\n" + ex.Message,
                        "影音库网页版",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return;
                }

                bool ready = false;
                DateTime deadline = DateTime.UtcNow.AddSeconds(90);
                DateTime? processExitDeadline = null;
                while (DateTime.UtcNow < deadline && !ready)
                {
                    Thread.Sleep(300);
                    if (
                        serviceProcess != null
                        && serviceProcess.HasExited
                        && !processExitDeadline.HasValue
                    )
                    {
                        // PyInstaller one-file executables may hand off to a
                        // child process and let the parent exit before the
                        // HTTP listener is ready.
                        processExitDeadline = DateTime.UtcNow.AddSeconds(10);
                    }
                    if (
                        processExitDeadline.HasValue
                        && DateTime.UtcNow >= processExitDeadline.Value
                    )
                    {
                        break;
                    }
                    ready = PingMatches(port, dataDir);
                }

                if (!ready)
                {
                    string detail = serviceProcess != null && serviceProcess.HasExited
                        ? "\n\n服务进程已退出，退出代码：" + serviceProcess.ExitCode
                        : "\n\n首次启动通常需要几秒，请检查安全软件是否拦截了影音库服务。";
                    MessageBox.Show(
                        "影音库服务没有及时启动。" + detail,
                        "影音库网页版",
                        MessageBoxButtons.OK,
                        MessageBoxIcon.Error
                    );
                    return;
                }
            }

            if (!PingMatches(port, dataDir))
            {
                string detail = serviceProcess != null && serviceProcess.HasExited
                    ? "\n\n服务进程已退出，退出代码：" + serviceProcess.ExitCode
                    : "\n\n首次启动通常需要几秒，请检查安全软件是否拦截了影音库服务。";
                MessageBox.Show(
                    "影音库服务没有及时启动。" + detail,
                    "影音库网页版",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return;
            }

            if (Environment.GetEnvironmentVariable("MEDIAVAULT_LAUNCHER_NO_BROWSER") == "1")
            {
                return;
            }

            Process.Start(new ProcessStartInfo("http://127.0.0.1:" + port + "/")
            {
                UseShellExecute = true,
            });
        }

        private static int FindAvailablePort()
        {
            for (int port = PortStart; port < PortEnd; port++)
            {
                TcpListener listener = null;
                try
                {
                    listener = new TcpListener(IPAddress.Loopback, port);
                    listener.Start();
                    return port;
                }
                catch
                {
                }
                finally
                {
                    if (listener != null)
                    {
                        listener.Stop();
                    }
                }
            }
            return 0;
        }

        private static int FindRunning(string expectedVaultRoot)
        {
            for (int port = PortStart; port < PortEnd; port++)
            {
                if (PingMatches(port, expectedVaultRoot)) return port;
            }
            return 0;
        }

        private static bool PingMatches(int port, string expectedVaultRoot)
        {
            try
            {
                using (WebClient client = new WebClient())
                {
                    client.Proxy = null;
                    client.Encoding = Encoding.UTF8;
                    string raw = client.DownloadString(
                        "http://127.0.0.1:" + port + "/api/ping"
                    );
                    Match app = Regex.Match(
                        raw,
                        "\"app\"\\s*:\\s*\"([^\"]+)\""
                    );
                    Match version = Regex.Match(
                        raw,
                        "\"version\"\\s*:\\s*\"([^\"]+)\""
                    );
                    Match protocol = Regex.Match(
                        raw,
                        "\"protocolVersion\"\\s*:\\s*(\\d+)"
                    );
                    Match instance = Regex.Match(
                        raw,
                        "\"instanceId\"\\s*:\\s*\"([^\"]+)\""
                    );
                    Match vault = Regex.Match(
                        raw,
                        "\"vaultRoot\"\\s*:\\s*\"((?:\\\\.|[^\"])*)\""
                    );
                    Guid parsedInstance;
                    return app.Success
                        && app.Groups[1].Value == AppId
                        && version.Success
                        && version.Groups[1].Value == ExpectedVersion
                        && protocol.Success
                        && protocol.Groups[1].Value
                            == ExpectedProtocolVersion.ToString()
                        && instance.Success
                        && Guid.TryParseExact(
                            instance.Groups[1].Value,
                            "N",
                            out parsedInstance
                        )
                        && vault.Success
                        && string.Equals(
                            Path.GetFullPath(
                                JsonUnescape(vault.Groups[1].Value)
                            ),
                            expectedVaultRoot,
                            StringComparison.OrdinalIgnoreCase
                        );
                }
            }
            catch
            {
                return false;
            }
        }

        private static string JsonUnescape(string value)
        {
            return value
                .Replace("\\\\", "\\")
                .Replace("\\\"", "\"");
        }
    }
}

using System;
using System.Diagnostics;
using System.IO;
using System.Net;
using System.Text.RegularExpressions;
using System.Threading;
using System.Windows.Forms;

namespace MediaVaultWebLauncher
{
    internal static class Program
    {
        private const string AppId = "media-vault";
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
            if (port == 0)
            {
                try
                {
                    Process.Start(new ProcessStartInfo(service)
                    {
                        Arguments = "--port " + PortStart
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

                DateTime deadline = DateTime.UtcNow.AddSeconds(40);
                while (DateTime.UtcNow < deadline && port == 0)
                {
                    Thread.Sleep(250);
                    port = FindRunning(dataDir);
                }
            }

            if (port == 0)
            {
                MessageBox.Show(
                    "影音库服务没有及时启动。\n\n请查看程序目录是否完整，然后重试。",
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

        private static int FindRunning(string expectedVaultRoot)
        {
            for (int port = PortStart; port < PortEnd; port++)
            {
                try
                {
                    using (WebClient client = new WebClient())
                    {
                        client.Proxy = null;
                        string raw = client.DownloadString(
                            "http://127.0.0.1:" + port + "/api/ping"
                        );
                        Match app = Regex.Match(
                            raw,
                            "\"app\"\\s*:\\s*\"([^\"]+)\""
                        );
                        Match vault = Regex.Match(
                            raw,
                            "\"vaultRoot\"\\s*:\\s*\"((?:\\\\.|[^\"])*)\""
                        );
                        if (app.Success
                            && app.Groups[1].Value == AppId
                            && vault.Success
                            && string.Equals(
                            Path.GetFullPath(
                                JsonUnescape(vault.Groups[1].Value)
                            ),
                            expectedVaultRoot,
                            StringComparison.OrdinalIgnoreCase
                        ))
                        {
                            return port;
                        }
                    }
                }
                catch
                {
                }
            }
            return 0;
        }

        private static string JsonUnescape(string value)
        {
            return value
                .Replace("\\\\", "\\")
                .Replace("\\\"", "\"");
        }
    }
}

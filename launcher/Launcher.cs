using System;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

namespace MediaVaultLauncher
{
    internal static class Program
    {
        [STAThread]
        private static void Main()
        {
            string baseDir = AppDomain.CurrentDomain.BaseDirectory;
            string appDir = Path.Combine(baseDir, "程序");
            string executable = Path.Combine(appDir, "影音库.exe");

            if (!File.Exists(executable))
            {
                MessageBox.Show(
                    "没有找到程序目录中的影音库.exe。\n\n请重新解压完整安装包。",
                    "影音库",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
                return;
            }

            try
            {
                Process.Start(new ProcessStartInfo(executable)
                {
                    WorkingDirectory = appDir,
                    UseShellExecute = true,
                });
            }
            catch (Exception ex)
            {
                MessageBox.Show(
                    "影音库启动失败。\n\n" + ex.Message,
                    "影音库",
                    MessageBoxButtons.OK,
                    MessageBoxIcon.Error
                );
            }
        }
    }
}

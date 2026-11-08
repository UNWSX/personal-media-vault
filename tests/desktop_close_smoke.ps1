param(
    [Parameter(Mandatory = $true)]
    [string]$Executable,

    [Parameter(Mandatory = $true)]
    [string]$NotePath,

    [string]$Marker = "desktop-close-smoke",

    [switch]$ExpectSaveFailure
)

$ErrorActionPreference = "Stop"
Add-Type -AssemblyName UIAutomationClient
Add-Type -AssemblyName UIAutomationTypes

$testPassed = $false
$process = Start-Process -FilePath $Executable -PassThru
try {
    $deadline = [DateTime]::UtcNow.AddSeconds(30)
    do {
        Start-Sleep -Milliseconds 250
        $process.Refresh()
    } while ($process.MainWindowHandle -eq 0 -and [DateTime]::UtcNow -lt $deadline)
    if ($process.MainWindowHandle -eq 0) {
        throw "Desktop window did not appear"
    }

    $root = [System.Windows.Automation.AutomationElement]::FromHandle($process.MainWindowHandle)
    $navigationDeadline = [DateTime]::UtcNow.AddSeconds(10)
    do {
        Start-Sleep -Milliseconds 250
        $randomCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
            "randomBtn"
        )
        $random = $root.FindFirst(
            [System.Windows.Automation.TreeScope]::Descendants,
            $randomCondition
        )
        if ($null -eq $random) {
            $randomCondition = New-Object System.Windows.Automation.PropertyCondition(
                [System.Windows.Automation.AutomationElement]::NameProperty,
                "随便看一部"
            )
            $random = $root.FindFirst(
                [System.Windows.Automation.TreeScope]::Descendants,
                $randomCondition
            )
        }
    } while ($null -eq $random -and [DateTime]::UtcNow -lt $navigationDeadline)
    if ($null -eq $random) {
        throw "Random button was not found"
    }
    $invoke = $random.GetCurrentPattern([System.Windows.Automation.InvokePattern]::Pattern)
    $invoke.Invoke()
    Start-Sleep -Milliseconds 500

    $windowPattern = $root.GetCurrentPattern(
        [System.Windows.Automation.WindowPattern]::Pattern
    )
    if ($ExpectSaveFailure) {
        $statusCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::AutomationIdProperty,
            "statusToggle"
        )
        $statusToggle = $root.FindFirst(
            [System.Windows.Automation.TreeScope]::Descendants,
            $statusCondition
        )
        if ($null -eq $statusToggle) {
            throw "Status toggle was not found"
        }
        $servicePath = Join-Path (Split-Path -Parent $Executable) "影音库服务.exe"
        Get-Process -Name "影音库服务" -ErrorAction SilentlyContinue |
            Where-Object { $_.Path -eq $servicePath } |
            Stop-Process -Force
        Start-Sleep -Milliseconds 300
        $statusInvoke = $statusToggle.GetCurrentPattern(
            [System.Windows.Automation.InvokePattern]::Pattern
        )
        $statusInvoke.Invoke()
        # No comment draft exists; close while the status save is failing.
        $windowPattern.Close()
        Start-Sleep -Seconds 2
        $process.Refresh()
        if ($process.HasExited) {
            throw "Desktop window closed despite a failed pending save"
        }
        Write-Output "desktop-close-failure-smoke: PASS"
        $testPassed = $true
    }
    else {
        $editCondition = New-Object System.Windows.Automation.PropertyCondition(
            [System.Windows.Automation.AutomationElement]::ControlTypeProperty,
            [System.Windows.Automation.ControlType]::Edit
        )
        $edits = $root.FindAll(
            [System.Windows.Automation.TreeScope]::Descendants,
            $editCondition
        )
        $editor = $null
        for ($i = 0; $i -lt $edits.Count; $i++) {
            if ($edits[$i].Current.Name -like "*自动保存*") {
                $editor = $edits[$i]
                break
            }
        }
        if ($null -eq $editor) {
            throw "Comment editor was not found"
        }

        $valuePattern = $editor.GetCurrentPattern(
            [System.Windows.Automation.ValuePattern]::Pattern
        )
        $valuePattern.SetValue($Marker)

        # Close immediately, without waiting for the 700ms debounce.
        $windowPattern.Close()
        if (-not $process.WaitForExit(30000)) {
            throw "Desktop window did not close after flushing pending text"
        }
        $saved = [IO.File]::ReadAllText($NotePath, [Text.Encoding]::UTF8)
        if (-not $saved.Contains($Marker)) {
            throw "Pending comment was not saved before the desktop window closed"
        }
        Write-Output "desktop-close-smoke: PASS"
        $testPassed = $true
    }
}
finally {
    if (-not $process.HasExited) {
        Stop-Process -Id $process.Id -Force -ErrorAction SilentlyContinue
    }
}

if ($testPassed) {
    exit 0
}
exit 1

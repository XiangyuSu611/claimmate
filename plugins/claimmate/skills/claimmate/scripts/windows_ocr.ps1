param(
    [Parameter(Mandatory = $true, Position = 0)]
    [string]$ImagePath
)

$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
Add-Type -AssemblyName System.Runtime.WindowsRuntime

$asTaskMethod = [System.WindowsRuntimeSystemExtensions].GetMethods() |
    Where-Object {
        $_.Name -eq "AsTask" -and
        $_.IsGenericMethod -and
        $_.GetParameters().Count -eq 1
    } |
    Select-Object -First 1

function Await-WinRtOperation {
    param(
        [Parameter(Mandatory = $true)]$Operation,
        [Parameter(Mandatory = $true)][Type]$ResultType
    )

    $asTask = $asTaskMethod.MakeGenericMethod($ResultType)
    $task = $asTask.Invoke($null, @($Operation))
    $task.Wait()
    return $task.Result
}

$storageFileType = [Windows.Storage.StorageFile, Windows.Storage, ContentType = WindowsRuntime]
$randomAccessStreamType = [Windows.Storage.Streams.IRandomAccessStream, Windows.Storage.Streams, ContentType = WindowsRuntime]
$bitmapDecoderType = [Windows.Graphics.Imaging.BitmapDecoder, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$softwareBitmapType = [Windows.Graphics.Imaging.SoftwareBitmap, Windows.Graphics.Imaging, ContentType = WindowsRuntime]
$ocrEngineType = [Windows.Media.Ocr.OcrEngine, Windows.Foundation, ContentType = WindowsRuntime]
$ocrResultType = [Windows.Media.Ocr.OcrResult, Windows.Foundation, ContentType = WindowsRuntime]
$fileAccessModeType = [Windows.Storage.FileAccessMode, Windows.Storage, ContentType = WindowsRuntime]

$engine = $ocrEngineType::TryCreateFromUserProfileLanguages()
if ($null -eq $engine) {
    throw "Windows OCR has no recognizer for the current user languages."
}

$resolvedPath = [System.IO.Path]::GetFullPath($ImagePath)
$file = Await-WinRtOperation ($storageFileType::GetFileFromPathAsync($resolvedPath)) $storageFileType
$stream = Await-WinRtOperation ($file.OpenAsync($fileAccessModeType::Read)) $randomAccessStreamType

try {
    $decoder = Await-WinRtOperation ($bitmapDecoderType::CreateAsync($stream)) $bitmapDecoderType
    $bitmap = Await-WinRtOperation ($decoder.GetSoftwareBitmapAsync()) $softwareBitmapType
    try {
        $result = Await-WinRtOperation ($engine.RecognizeAsync($bitmap)) $ocrResultType
        [Console]::Write($result.Text)
    }
    finally {
        if ($null -ne $bitmap) {
            $bitmap.Dispose()
        }
    }
}
finally {
    if ($null -ne $stream) {
        $stream.Dispose()
    }
}

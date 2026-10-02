; Manual BSGO control overlay. No input is sent until a button is clicked.
; Focus stays with the game; commands abort if foreground changes mid-chord.
#include <GUIConstantsEx.au3>
#include <WindowsStylesConstants.au3>

Opt("MustDeclareVars", 1)
Opt("SendKeyDelay", 10)
Opt("SendKeyDownDelay", 80)
Opt("SendCapslockMode", 0)

Global Const $OVERLAY_NO_ACTIVATE = 0x08000000
Global Const $OVERLAY_MOUSE_ACTIVATE = 0x0021
Global Const $OVERLAY_MA_NO_ACTIVATE = 3
Global $gShiftOwned = "", $gKeyOwned = ""
Global $gHoldMs = 100, $gBusy = False, $gLastAction = TimerInit()
Global $gOverlay = GUICreate("BSGO controls", 342, 274, 30, 60, _
    BitOR($WS_CAPTION, $WS_SYSMENU), _
    BitOR($WS_EX_TOPMOST, $WS_EX_TOOLWINDOW, $OVERLAY_NO_ACTIVATE))
If Not $gOverlay Then Exit 1
GUIRegisterMsg($OVERLAY_MOUSE_ACTIVATE, "_NoActivate")
OnAutoItExitRegister("_ReleaseOwnedKeys")

Global $gLeftMap = GUICtrlCreateButton("2D map: Left Shift + M", 10, 10, 322, 34)
Global $gRightMap = GUICtrlCreateButton("2D map: Right Shift + M", 10, 50, 322, 34)
Global $gMap = GUICtrlCreateButton("Map / close: M", 10, 90, 156, 32)
Global $gCamera = GUICtrlCreateButton("Free camera: Shift + F", 176, 90, 156, 32)
Global $gScan = GUICtrlCreateButton("Scan: Z", 10, 128, 156, 32)
Global $gGuns = GUICtrlCreateButton("Assign guns: G", 176, 128, 156, 32)
Global $gIdle = GUICtrlCreateButton("Idle throttle: PageDown", 10, 166, 322, 32)
Global $gTiming = GUICtrlCreateButton("Key timing: 100 ms", 10, 204, 156, 28)
Global $gQuit = GUICtrlCreateButton("Close overlay", 176, 204, 156, 28)
Global $gStatus = GUICtrlCreateLabel("Select BSGO, then click a command.", 10, 240, 322, 24)
GUISetState(@SW_SHOWNOACTIVATE, $gOverlay)

Global $gMessage
While True
    $gMessage = GUIGetMsg()
    Switch $gMessage
        Case $GUI_EVENT_CLOSE, $gQuit
            ExitLoop
        Case $gTiming
            Switch $gHoldMs
                Case 100
                    $gHoldMs = 250
                Case 250
                    $gHoldMs = 500
                Case Else
                    $gHoldMs = 100
            EndSwitch
            GUICtrlSetData($gTiming, "Key timing: " & $gHoldMs & " ms")
        Case $gLeftMap
            _Command("Left Shift + M", "m", "LSHIFT")
        Case $gRightMap
            _Command("Right Shift + M", "m", "RSHIFT")
        Case $gMap
            _Command("M", "m")
        Case $gCamera
            _Command("Left Shift + F", "f", "LSHIFT")
        Case $gScan
            _Command("Z", "z")
        Case $gGuns
            _Command("G", "g")
        Case $gIdle
            _Command("PageDown", "PGDN")
    EndSwitch
WEnd
GUIDelete($gOverlay)
Exit

Func _NoActivate($hWnd, $iMessage, $wParam, $lParam)
    Return $OVERLAY_MA_NO_ACTIVATE
EndFunc

Func _ActiveGame()
    Local $hActive = WinGetHandle("[ACTIVE]")
    If $hActive = "" Then Return 0
    If WinGetTitle($hActive) <> "Battlestar Galactica Online" Then Return 0
    Local $iPid = WinGetProcess($hActive)
    Local $aProcesses = ProcessList("bsgo.exe")
    Local $i
    For $i = 1 To $aProcesses[0][0]
        If $aProcesses[$i][1] = $iPid Then Return $hActive
    Next
    Return 0
EndFunc

Func _Command($sLabel, $sKey, $sShift = "")
    If $gBusy Or TimerDiff($gLastAction) < 800 Then Return
    Local $hGame = _ActiveGame()
    If Not $hGame Then
        GUICtrlSetData($gStatus, "No keys sent: BSGO must be active.")
        Return
    EndIf
    $gBusy = True
    GUICtrlSetData($gStatus, "Sending " & $sLabel & "...")
    If $sShift <> "" Then
        $gShiftOwned = $sShift
        Send("{" & $sShift & " down}")
        Sleep($gHoldMs)
    EndIf
    If Not WinActive($hGame) Then
        _ReleaseOwnedKeys()
        GUICtrlSetData($gStatus, "Aborted: game lost focus.")
    Else
        $gKeyOwned = $sKey
        Send("{" & $sKey & " down}")
        Sleep($gHoldMs)
        ; Release owned keys even if focus changes: never leave a modifier held.
        Send("{" & $sKey & " up}")
        $gKeyOwned = ""
        If $sShift <> "" Then Sleep($gHoldMs)
        _ReleaseOwnedKeys()
        GUICtrlSetData($gStatus, "Sent " & $sLabel & "; verify the game view.")
    EndIf
    $gLastAction = TimerInit()
    $gBusy = False
EndFunc

Func _ReleaseOwnedKeys()
    If $gKeyOwned <> "" Then Send("{" & $gKeyOwned & " up}")
    If $gShiftOwned <> "" Then Send("{" & $gShiftOwned & " up}")
    $gKeyOwned = ""
    $gShiftOwned = ""
EndFunc

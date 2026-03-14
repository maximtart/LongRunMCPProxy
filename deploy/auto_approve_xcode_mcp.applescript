-- auto_approve_xcode_mcp.applescript
-- Watches for Xcode MCP agent permission dialogs and auto-clicks "Allow".
-- The dialog is an unnamed window with "Allow" button.
--
-- Usage:  osascript deploy/auto_approve_xcode_mcp.applescript &
-- Requires: Accessibility access

on run
	repeat
		try
			tell application "System Events"
				if exists process "Xcode" then
					tell process "Xcode"
						repeat with w in (every window)
							try
								set wName to name of w
								if wName is "" or wName is missing value then
									if exists button "Allow" of w then
										click button "Allow" of w
										do shell script "logger -t xcode-mcp-approver 'Auto-approved MCP agent dialog'"
									end if
								end if
							end try
						end repeat
					end tell
				end if
			end tell
		end try
		delay 0.5
	end repeat
end run

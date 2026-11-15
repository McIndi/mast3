# This file is part of McIndi's Automated Solutions Tool (MAST).
#
# MAST is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 3
# as published by the Free Software Foundation.
#
# MAST is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with MAST.  If not, see <https://www.gnu.org/licenses/>.
#
# Copyright 2015-2024, McIndi Solutions, All rights reserved.
"""
This module is meant to consolidate the functionality of the following
plugins:

    1_system
    2_accounts
    3_backups
    4_developer
    5_network

This is possible since, even though the functionality between these plugins
is completely different, the structure is identical. In essence these plugins
dynamically create forms based on the function signatures of the functions
within the corresponding bin scripts.

The point of this is to recreate the functionality of the MAST CLI in the MAST
web GUI. So, I was able to consolidate all of the functionality of these
plugins into this single module.
"""
import re
import os
import sys
import flask
import inspect
import zipfile
import markdown
from markupsafe import Markup
from html import escape as html_escape
import html.entities as html_entities
from textwrap import dedent
from mast.config import get_config
from mast.datapower.datapower import Environment
from mast.xor import xordecode, xorencode
from mast.timestamp import Timestamp
from mast.logging import make_logger, logged
from mast.cli import Result, captured
from mast.cli import params as cli_params
from mast.plugin_utils import params as widgets
from pydantic import ValidationError

OBJECT_STATUS_ARGS = [
	"AAAPolicy",
	"Domain",
	"LDAPSearchParameters",
	"ProcessingMetadata",
	"RADIUSSettings",
	"RBMSettings",
	"SAMLAttributes",
	"SOAPHeaderDisposition",
	"TAM",
	"TFIMEndpoint",
	"XACMLPDP",
	"AccessControlList",
	"AccessProfile",
	"AMQPBroker",
	"AnalyticsEndpoint",
	"APIApplicationType",
	"APICollection",
	"APIConnectGatewayService",
	"APIDebugProbe",
	"APIDefinition",
	"APIGateway",
	"APIOperation",
	"APIPath",
	"APIPlan",
	"APISchema",
	"APISecurityAPIKey",
	"APISecurityBasicAuth",
	"APISecurityOAuthReq",
	"APISecurityOAuth",
	"APISecurityRequirement",
	"APISecurityTokenManager",
	"APIAuthURLRegistry",
	"APILDAPRegistry",
	"AppSecurityPolicy",
	"Assembly",
	"AssemblyFunction",
	"AuditLog",
	"B2BCPA",
	"B2BCPACollaboration",
	"B2BCPAReceiverSetting",
	"B2BCPASenderSetting",
	"B2BGateway",
	"B2BPersistence",
	"B2BProfile",
	"B2BProfileGroup",
	"B2BXPathRoutingPolicy",
	"WXSGrid",
	"XC10Grid",
	"CloudConnectorService",
	"CloudGatewayService",
	"CompactFlash",
	"CompileOptionsPolicy",
	"CompileSettings",
	"ConfigDeploymentPolicy",
	"ConfigSequence",
	"ConformancePolicy",
	"ControlList",
	"CORSPolicy",
	"CORSRule",
	"AAAJWTGenerator",
	"AAAJWTValidator",
	"CertMonitor",
	"CookieAttributePolicy",
	"CRLFetch",
	"CryptoCertificate",
	"CryptoFWCred",
	"CryptoIdentCred",
	"CryptoKerberosKDC",
	"CryptoKerberosKeytab",
	"CryptoKey",
	"CryptoProfile",
	"CryptoSSKey",
	"CryptoValCred",
	"JOSERecipientIdentifier",
	"JOSESignatureIdentifier",
	"JWEHeader",
	"JWERecipient",
	"JWSSignature",
	"OAuthSupportedClient",
	"OAuthSupportedClientGroup",
	"SocialLoginPolicy",
	"SSHClientProfile",
	"SSHDomainClientProfile",
	"SSHServerProfile",
	"SSLClientProfile",
	"SSLProxyProfile",
	"SSLServerProfile",
	"SSLSNIMapping",
	"SSLSNIServerProfile",
	"DeploymentPolicyParametersBinding",
	"ErrorReportSettings",
	"SystemSettings",
	"TimeSettings",
	"DFDLSettings",
	"DomainAvailability",
	"DomainSettings",
	"SchemaExceptionMap",
	"DocumentCryptoMap",
	"XPathRoutingMap",
	"LogTarget",
	"FormsLoginPolicy",
	"FTPQuoteCommands",
	"MultiProtocolGateway",
	"WSGateway",
	"GatewayPeering",
	"GatewayPeeringManager",
	"GeneratedPolicy",
	"GraphQLSchemaOptions",
	"GWScriptSettings",
	"HTTPInputConversionMap",
	"HTTPUserAgent",
	"ILMTScanner",
	"ImportPackage",
	"IMSConnect",
	"IncludeConfig",
	"InteropService",
	"EthernetInterface",
	"LinkAggregation",
	"VLANInterface",
	"IPMILanChannel",
	"IPMIUser",
	"IPMulticast",
	"ISAMReverseProxy",
	"ISAMReverseProxyJunction",
	"ISAMRuntime",
	"IScsiChapConfig",
	"IScsiHBAConfig",
	"IScsiInitiatorConfig",
	"IScsiTargetConfig",
	"IScsiVolumeConfig",
	"TibcoEMSServer",
	"WebSphereJMSServer",
	"JSONSettings",
	"KafkaCluster",
	"Language",
	"LDAPConnectionPool",
	"LoadBalancerGroup",
	"LogLabel",
	"Luna",
	"LunaHAGroup",
	"LunaHASettings",
	"LunaPartition",
	"Matching",
	"MCFCustomRule",
	"MCFHttpHeader",
	"MCFHttpMethod",
	"MCFHttpURL",
	"MCFXPath",
	"MessageContentFilters",
	"FilterAction",
	"MessageMatching",
	"CountMonitor",
	"DurationMonitor",
	"MessageType",
	"MPGWErrorAction",
	"MPGWErrorHandlingPolicy",
	"MQGW",
	"MQhost",
	"MQManager",
	"MQManagerGroup",
	"MQproxy",
	"MQQM",
	"MQQMGroup",
	"MTOMPolicy",
	"NameValueProfile",
	"DNSNameService",
	"HostAlias",
	"NetworkSettings",
	"NTPService",
	"NFSClientSettings",
	"NFSDynamicMounts",
	"NFSStaticMount",
	"OAuthProviderSettings",
	"ODR",
	"ODRConnectorGroup",
	"OperationRateLimit",
	"ParseSettings",
	"PasswordAlias",
	"Pattern",
	"PeerGroup",
	"PolicyAttachments",
	"PolicyParameters",
	"ProductInsights",
	"QuotaEnforcementServer",
	"RaidVolume",
	"RateLimitConfiguration",
	"RateLimitDefinition",
	"SQLRuntimeSettings",
	"SecureBackupMode",
	"SecureCloudConnector",
	"SecureGatewayClient",
	"GWSRemoteDebug",
	"MgmtInterface",
	"RestMgmtInterface",
	"SSHService",
	"TelnetService",
	"WebB2BViewer",
	"WebGUI",
	"XMLFirewallService",
	"XSLProxyService",
	"HTTPService",
	"SSLProxyService",
	"TCPProxyService",
	"XSLCoprocService",
	"ShellAlias",
	"SimpleCountMonitor",
	"SLMAction",
	"SLMCredClass",
	"SLMPolicy",
	"SLMRsrcClass",
	"SLMSchedule",
	"SMTPServerConnection",
	"SNMPSettings",
	"AMQPSourceProtocolHandler",
	"AS2ProxySourceProtocolHandler",
	"AS2SourceProtocolHandler",
	"AS3SourceProtocolHandler",
	"EBMS2SourceProtocolHandler",
	"EBMS3SourceProtocolHandler",
	"FTPFilePollerSourceProtocolHandler",
	"NFSFilePollerSourceProtocolHandler",
	"SFTPFilePollerSourceProtocolHandler",
	"FTPServerSourceProtocolHandler",
	"HTTPSourceProtocolHandler",
	"HTTPSSourceProtocolHandler",
	"IMSCalloutSourceProtocolHandler",
	"IMSConnectSourceProtocolHandler",
	"TibcoEMSSourceProtocolHandler",
	"WebSphereJMSSourceProtocolHandler",
	"KafkaSourceProtocolHandler",
	"MQFTESourceProtocolHandler",
	"MQSourceProtocolHandler",
	"MQv9PlusMFTSourceProtocolHandler",
	"MQv9PlusSourceProtocolHandler",
	"AS1PollerSourceProtocolHandler",
	"POPPollerSourceProtocolHandler",
	"SSHServerSourceProtocolHandler",
	"StatelessTCPSourceProtocolHandler",
	"XTCProtocolHandler",
	"SQLDataSource",
	"StandaloneStandbyControl",
	"StandaloneStandbyControlInterface",
	"Statistics",
	"StylePolicy",
	"APIClientIdentification",
	"APICORS",
	"APIExecute",
	"APIRateLimit",
	"APIResult",
	"APIRouting",
	"APISecurity",
	"AssemblyActionClientSecurity",
	"AssemblyActionFunctionCall",
	"AssemblyActionGatewayScript",
	"AssemblyActionGraphQLIntrospect",
	"AssemblyActionHtmlPage",
	"AssemblyActionInvoke",
	"AssemblyActionJson2Xml",
	"AssemblyActionJWTGenerate",
	"AssemblyActionJWTValidate",
	"AssemblyActionLog",
	"AssemblyActionMap",
	"AssemblyActionOAuth",
	"AssemblyActionParse",
	"AssemblyActionRateLimit",
	"AssemblyActionRedact",
	"AssemblyActionSetVar",
	"AssemblyActionUserSecurity",
	"AssemblyActionValidate",
	"AssemblyActionWebSocketUpgrade",
	"AssemblyActionWSDL",
	"AssemblyActionXml2Json",
	"AssemblyActionXSLT",
	"AssemblyActionThrow",
	"AssemblyLogicOperationSwitch",
	"AssemblyLogicSwitch",
	"StylePolicyAction",
	"APIRule",
	"StylePolicyRule",
	"WSStylePolicyRule",
	"Tenant",
	"Throttler",
	"UDDIRegistry",
	"URLMap",
	"URLRefreshPolicy",
	"URLRewritePolicy",
	"User",
	"UserGroup",
	"VisibilityList",
	"WCCService",
	"WebAppErrorHandlingPolicy",
	"WebAppFW",
	"WebAppRequest",
	"WebAppResponse",
	"WebAppSessionPolicy",
	"WebServiceMonitor",
	"WebServicesAgent",
	"UDDISubscription",
	"WSRRSavedSearchSubscription",
	"WSRRSubscription",
	"WebTokenService",
	"WSEndpointRewritePolicy",
	"WSRRServer",
	"WSStylePolicy",
	"XMLManager",
	"xmltrace",
	"ZHybridTargetControlService",
	"ZosNSSClient",
]

STATUS_PROVIDERS = [
    "ActiveUsers",
    "AMQPBrokerStatus",
    "AMQPSourceProtocolHandlerSummary",
    "APIDocumentCachingSummary",
    "APIDocumentStatusSimpleIndex",
    "APIOAuthCachesStatus",
    "APIStylesheetCachingSummary",
    "APIStylesheetProfilesSimpleIndex",
    "APIStylesheetStatusSimpleIndex",
    "APISubscriberCacheStatus",
    "APISubscriberStatus",
    "ARPStatus",
    "AS1PollerSourceProtocolHandlerSummary",
    "AS2SourceProtocolHandlerSummary",
    "AS3SourceProtocolHandlerSummary",
    "AuthCookieCacheStatus",
    "B2BGatewaySummary",
    "B2BHighAvailabilityStatus",
    "B2BMessageArchiveStatus",
    "B2BMPCStatus",
    "B2BMPCStatus2",
    "B2BTransactionLog",
    "B2BTransactionLog2",
    "Battery",
    "ChangeGroupRetryQueue",
    "ChangeGroups",
    "CloudConnectorServiceSummary",
    "CloudGatewayServiceSummary",
    "ConfigSequenceStatus",
    "ConnectionsAccepted",
    "CountLimitAssemblyStatus",
    "CPUUsage",
    "CryptoEngineStatus",
    "CryptoEngineStatus2",
    "CryptoHwDisableStatus",
    "CryptoModeStatus",
    "CurrentSensors",
    "DateTimeStatus",
    "DateTimeStatus2",
    "DebugActionStatus",
    "DNSCacheHostStatus",
    "DNSCacheHostStatus2",
    "DNSCacheHostStatus3",
    "DNSCacheHostStatus4",
    "DNSNameServerStatus",
    "DNSNameServerStatus2",
    "DNSSearchDomainStatus",
    "DNSStaticHostStatus",
    "DocumentCachingSummary",
    "DocumentCachingSummaryGlobal",
    "DocumentStatus",
    "DocumentStatusSimpleIndex",
    "DomainCheckpointStatus",
    "DomainsMemoryStatus",
    "DomainsMemoryStatus2",
    "DomainStatus",
    "DomainSummary",
    "DynamicQueueManager",
    "DynamicTibcoEMSStatus",
    "EBMS2SourceProtocolHandlerSummary",
    "EBMS3SourceProtocolHandlerSummary",
    "EnvironmentalFanSensors",
    "EnvironmentalSensors",
    "EthernetCountersStatus",
    "EthernetInterfaceStatus",
    "EthernetMAUStatus",
    "EthernetMIIRegisterStatus",
    "FailureNotificationStatus",
    "FailureNotificationStatus2",
    "FibreChannelLuns",
    "FibreChannelVolumeStatus",
    "FilePollerStatus",
    "FilesystemStatus",
    "FirmwareStatus",
    "FirmwareStatus2",
    "FirmwareVersion",
    "FirmwareVersion2",
    "FirmwareVersion3",
    "FTPFilePollerSourceProtocolHandlerSummary",
    "FTPServerSourceProtocolHandlerSummary",
    "GatewayPeeringCacheStatus",
    "GatewayPeeringClusterStatus",
    "GatewayPeeringKeyStatus",
    "GatewayPeeringStatus",
    "GatewayScriptStatus",
    "GatewayTransactions",
    "GraphQLStatus",
    "HSMKeyStatus",
    "HTTPConnections",
    "HTTPConnectionsCreated",
    "HTTPConnectionsDestroyed",
    "HTTPConnectionsOffered",
    "HTTPConnectionsRequested",
    "HTTPConnectionsReturned",
    "HTTPConnectionsReused",
    "HTTPMeanTransactionTime",
    "HTTPMeanTransactionTime2",
    "HTTPServiceSummary",
    "HTTPSourceProtocolHandlerSummary",
    "HTTPSSourceProtocolHandlerSummary",
    "HTTPTransactions",
    "HTTPTransactions2",
    "Hypervisor",
    "Hypervisor2",
    "Hypervisor3",
    "IGMPStatus",
    "IMSConnectSourceProtocolHandlerSummary",
    "IMSConnectstatus",
    "IPAddressStatus",
    "IPMISelEvents",
    "IPMulticastStatus",
    "IScsiHBAStatus",
    "IScsiInitiatorStatus",
    "IScsiTargetStatus",
    "IScsiVolumeStatus",
    "KafkaClusterStatus",
    "KafkaSourceProtocolHandlerSummary",
    "KerberosTickets",
    "KerberosTickets2",
    "LDAPPoolEntries",
    "LDAPPoolSummary",
    "LibraryVersion",
    "LicenseStatus",
    "LinkAggregationMemberStatus",
    "LinkAggregationStatus",
    "LinkStatus",
    "LoadBalancerStatus",
    "LoadBalancerStatus2",
    "LogTargetStatus",
    "LunaLatency",
    "MemoryStatus",
    "MessageCountFilters",
    "MessageCounts",
    "MessageDurationFilters",
    "MessageDurations",
    "MessageSources",
    "MQConnStatus",
    "MQFTESourceProtocolHandlerSummary",
    "MQManagerConvStatus",
    "MQManagerStatus",
    "MQQMstatus",
    "MQSourceProtocolHandlerSummary",
    "MQStatus",
    "MQSystemResources",
    "MQv9PlusMFTSourceProtocolHandlerSummary",
    "MQv9PlusSourceProtocolHandlerSummary",
    "MultiProtocolGatewaySummary",
    "NDCacheStatus",
    "NDCacheStatus2",
    "NetworkInterfaceStatus",
    "NetworkReceiveDataThroughput",
    "NetworkReceivePacketThroughput",
    "NetworkTransmitDataThroughput",
    "NetworkTransmitPacketThroughput",
    "NFSFilePollerSourceProtocolHandlerSummary",
    "NFSMountStatus",
    "NTPRefreshStatus",
    "OAuthCachesStatus",
    "ObjectStatus",
    "ODRConnectorGroupStatus",
    "ODRConnectorGroupStatus2",
    "ODRLoadBalancerStatus",
    "OtherSensors",
    "PCIBus",
    "PolicyDomainStatus",
    "POPPollerSourceProtocolHandlerSummary",
    "PortStatus",
    "PowerSensors",
    "QueueManagersStatus",
    "QuotaEnforcementStatus",
    "RaidArrayStatus",
    "RaidBatteryBackUpStatus",
    "RaidBatteryModuleStatus",
    "RaidLogicalDriveStatus",
    "RaidPartitionStatus",
    "RaidPhysDiskStatus",
    "RaidPhysDiskStatus2",
    "RaidPhysicalDriveStatus",
    "RaidSsdStatus",
    "RaidVolumeStatus",
    "RaidVolumeStatus2",
    "RateLimitAPIStatus",
    "RateLimitAssemblyStatus",
    "RateLimitConcurrentStatus",
    "RateLimitCountStatus",
    "RateLimitRateStatus",
    "RateLimitTokenBucketStatus",
    "ReceiveKbpsThroughput",
    "ReceivePacketThroughput",
    "RoutingStatus",
    "RoutingStatus2",
    "RoutingStatus3",
    "SecureCloudConnectorConnectionsStatus",
    "SelfBalancedStatus",
    "SelfBalancedStatus2",
    "SelfBalancedTable",
    "ServicesMemoryStatus",
    "ServicesMemoryStatus2",
    "ServicesStatus",
    "ServicesStatusPlus",
    "ServiceVersionStatus",
    "SFTPFilePollerSourceProtocolHandlerSummary",
    "SGClientConnectionStatus",
    "SGClientStatus",
    "SLMPeeringStatus",
    "SLMSummaryStatus",
    "SNMPStatus",
    "SQLConnectionPoolStatus",
    "SQLRuntimeStatus",
    "SQLStatus",
    "SSHKnownHostFileStatus",
    "SSHKnownHostFileStatus2",
    "SSHKnownHostStatus",
    "SSHServerSourceProtocolHandlerSummary",
    "SSHTrustedHostStatus",
    "SSLProxyServiceSummary",
    "StandbyStatus",
    "StandbyStatus2",
    "StatelessTCPSourceProtocolHandlerSummary",
    "StylesheetCachingSummary",
    "StylesheetCachingSummary2",
    "StylesheetExecutions",
    "StylesheetExecutionsSimpleIndex",
    "StylesheetMeanExecutionTime",
    "StylesheetMeanExecutionTimeSimpleIndex",
    "StylesheetProfiles",
    "StylesheetProfilesSimpleIndex",
    "StylesheetStatus",
    "StylesheetStatusSimpleIndex",
    "SystemCpuStatus",
    "SystemMemoryStatus",
    "SystemUsage",
    "SystemUsage2Table",
    "SystemUsageTable",
    "TCPProxyServiceSummary",
    "TCPSummary",
    "TCPTable",
    "TemperatureSensors",
    "TenantLicenses",
    "TenantMemory",
    "TibcoEMSSourceProtocolHandlerSummary",
    "TibcoEMSStatus",
    "TransmitKbpsThroughput",
    "TransmitPacketThroughput",
    "UDDISubscriptionKeyStatusSimpleIndex",
    "UDDISubscriptionServiceStatusSimpleIndex",
    "UDDISubscriptionStatusSimpleIndex",
    "Version",
    "VirtualPlatform",
    "VirtualPlatform2",
    "VirtualPlatform3",
    "VlanInterfaceStatus",
    "VlanInterfaceStatus2",
    "VoltageSensors",
    "WebAppFwAccepted",
    "WebAppFwRejected",
    "WebAppFWSummary",
    "WebSocketConnStatus",
    "WebSphereJMSSourceProtocolHandlerSummary",
    "WebSphereJMSStatus",
    "WebTokenServiceSummary",
    "WSGatewaySummary",
    "WSMAgentSpoolers",
    "WSMAgentStatus",
    "WSOperationMetrics",
    "WSOperationMetricsSimpleIndex",
    "WSOperationsStatus",
    "WSOperationsStatusSimpleIndex",
    "WSRRSavdSrchSubsPolicyAttachmentsStatus",
    "WSRRSavedSearchSubscriptionServiceStatus",
    "WSRRSavedSearchSubscriptionStatus",
    "WSRRSubscriptionPolicyAttachmentsStatus",
    "WSRRSubscriptionServiceStatus",
    "WSRRSubscriptionStatus",
    "WSWSDLStatus",
    "WSWSDLStatusSimpleIndex",
    "WXSGridStatus",
    "XC10GridStatus",
    "XMLFirewallServiceSummary",
    "XMLNamesStatus",
    "XSLCoprocServiceSummary",
    "XSLProxyServiceSummary",
    "XTCProtocolHandlerSummary",
    "ZHybridTCSstatus",
    "ZosNSSstatus"
]

def _zipdir(path, z):
    """Create a zip file z of all files in path recursively"""
    for root, _, files in os.walk(path):
        for f in files:
            filename = os.path.join(
                *os.path.join(root, f).split(os.path.sep)[2:])
            z.write(os.path.join(root, f), filename)


def get_module(plugin):
    """Return the imported objects which correspond to plugin.
    These are all from bin (which is a module itself)."""
    module = __import__("mast.datapower", globals(), locals(), [plugin], 0)
    return getattr(module, plugin)


def unescape(text):
    """Removes HTML or XML character references and entities from a text string.
    @param text The HTML (or XML) source text.
    @return The plain text, as a Unicode string, if necessary.

    This function was taken from:
    http://effbot.org/zone/re-sub.htm#unescape-html

    written by: Fredrik Lundh"""

    def fixup(m):
        text = m.group(0)
        if text[:2] == "&#":
            # character reference
            try:
                if text[:3] == "&#x":
                    return chr(int(text[3:-1], 16))
                else:
                    return chr(int(text[2:-1]))
            except ValueError:
                pass
        else:
            # named entity
            try:
                text = chr(html_entities.name2codepoint[text[1:-1]])
            except KeyError:
                pass
        return text  # leave as is
    return re.sub(r"&#?\w+;", fixup, text)



def html(plugin):
    """Return the html for plugin's tab"""
    htm = []
    module = get_module(plugin.replace("mast.datapower.", ""))
    last_category = ''
    command_list = module.cli.categories()
    for category in sorted(command_list):
        for command in command_list[category]:
            callable_name = command.key.replace('_', ' ')
            if category != last_category:
                htm.append(
                    flask.render_template(
                        'categorylabel.html', category=category))
            htm.append(
                flask.render_template(
                    'dynbutton.html', plugin=plugin, callable=callable_name))
            last_category = category
    return unescape(flask.render_template(
        'dynplugin.html', plugin=plugin, buttons=''.join(htm)))


def _get_command(plugin, fn_name):
    """Return the `mast.cli.Command` of plugin whose function is named
    fn_name."""
    return get_module(plugin).cli.find(fn_name)


def _get_arguments(plugin, fn_name):
    """Return a list of two-tuples containing the argument names and
    default values for function name and the actual function. This is
    for legacy commands, which declare their parameters with their
    signature."""
    item = _get_command(plugin, fn_name).fn
    args, _, __, defaults, ___, ____, _____ = inspect.getfullargspec(item)
    return (list(zip(args, defaults)), item)


def render_textbox(key, value):
    """Render a textbox for a dynamic form."""
    name = key
    label = key.replace('_', ' ')
    return flask.render_template(
        "textbox.html", name=name,
        label=label, value=value)


def render_password_box(key, value):
    """Render a textbox for a dynamic form."""
    name = key
    label = key.replace('_', ' ')
    return flask.render_template(
        "passwordbox.html", name=name,
        label=label, value=value)


def render_checkbox(key, checked=False):
    """Render a checkbox for a dynamic form."""
    name = key
    label = key.replace('_', ' ')
    checked = "checked=checked" if checked else ""
    return flask.render_template(
        "checkbox.html", name=name,
        label=label, checked=checked)


def render_multitext(key):
    """Render a multi-value textbox for a dynamic form."""
    _id = key
    label = key.replace('_', ' ')
    return flask.render_template("multitext.html", id=_id, label=label)


def render_file_upload(plugin, key):
    """Render our custom file upload form control for a dynamic form."""
    name = key
    label = key.replace('_', ' ')
    return flask.render_template(
        "fileupload.html", name=name,
        label=label, plugin=plugin)


def render_select_object_status(key, env):
    options = env.common_config(key)
    return flask.render_template(
        'dynselect.html',
        options=options,
        name=key,
        disclaimer=True)


def render_multiselect_object_status(key, env):
    options = env.common_config(key)
    return flask.render_template(
        "multiselect.html",
        options=options,
        key=key,
        disclaimer=True)


def render_multiselect_status_provider(key):
    return flask.render_template("multiselect.html", options=STATUS_PROVIDERS,
        key=key, disclaimer=False)


def render_select_status_provider(key):
    return flask.render_template("dynselect.html", options=STATUS_PROVIDERS,
        name=key, disclaimer=False)


def render_multiselect_object_class(key):
    return flask.render_template("multiselect.html", options=OBJECT_STATUS_ARGS,
        key=key, disclaimer=False)


def render_select_object_class(key):
    return flask.render_template("dynselect.html", options=OBJECT_STATUS_ARGS,
        name=key, disclaimer=False)


def _model_form(plugin, command, env):
    """Return the form for a command that declares its parameters with
    a model. Each control comes from the type and the widget of one
    parameter."""
    textboxes = []
    checkboxes = []
    file_uploads = []
    selects = []
    for spec in cli_params.options(command.model):
        key, widget, many = spec.name, spec.widget or "", spec.kind == "list"
        if widget in (widgets.APPLIANCES, widgets.CREDENTIALS,
                      widgets.OUT_DIR, widgets.OUT_FILE):
            # The web GUI supplies these, see _model_values
            continue
        elif widget == widgets.UPLOAD:
            file_uploads.append(render_file_upload(plugin, key))
        elif widget.startswith("configured:"):
            selects.append(_render_select(
                key, env.common_config(widget.split(":", 1)[1]),
                many, disclaimer=True))
        elif widget == widgets.STATUS_PROVIDER:
            selects.append(_render_select(key, STATUS_PROVIDERS, many))
        elif widget == widgets.OBJECT_CLASS:
            selects.append(_render_select(key, OBJECT_STATUS_ARGS, many))
        elif spec.kind == "flag":
            checkboxes.append(render_checkbox(key, checked=bool(spec.default)))
        elif spec.choices:
            selects.append(_render_select(key, spec.choices, many))
        elif many:
            textboxes.append(render_multitext(key))
        elif spec.secret:
            textboxes.append(render_password_box(key, spec.default or ""))
        else:
            textboxes.append(render_textbox(
                key, "" if spec.default is None else spec.default))
    return textboxes + selects + file_uploads + checkboxes


def _render_select(key, options, many=False, disclaimer=False):
    if many:
        return flask.render_template(
            "multiselect.html", options=options, key=key,
            disclaimer=disclaimer)
    return flask.render_template(
        "dynselect.html", options=options, name=key, disclaimer=disclaimer)


def get_form(plugin, fn_name, appliances, credentials, no_check_hostname=True):
    """Return a form suitable for gathering arguments to function name"""
    check_hostname = not no_check_hostname
    command = _get_command(plugin, fn_name)
    if command.model is not None:
        env = Environment(appliances, credentials, check_hostname=check_hostname)
        forms = ['<div class="{0}Form"><div name="{1}">'.format(plugin, fn_name)]
        forms.append(flask.render_template(
            'formlabel.html', label=fn_name.replace('_', ' ')))
        forms.append('<a href="#" class="help">help</a>')
        forms.append('<div class="hidden help_content">{}</div>'.format(
            Markup(markdown.markdown(command.doc))))
        forms.extend(_model_form(plugin, command, env))
        forms.append(flask.render_template('submitbutton.html', plugin=plugin))
        forms.append('</div></div>')
        return '<br />\n'.join(forms)
    textboxes = []
    checkboxes = []
    file_uploads = []
    selects = []

    env = Environment(appliances, credentials, check_hostname=check_hostname)

    forms = ['<div class="{0}Form"><div name="{1}">'.format(plugin, fn_name)]

    label = fn_name.replace('_', ' ')
    forms.append(flask.render_template('formlabel.html', label=label))

    #md = markdown.Markdown(extensions=['markdown.extensions.extra'])
    arguments, fn = _get_arguments(plugin, fn_name)
    forms.append('<a href="#" class="help">help</a>')
    forms.append('<div class="hidden help_content">{}</div>'.format(
        Markup(markdown.markdown(dedent(str(fn.__doc__))))))
    for arg in arguments:
        key, value = arg
        if isinstance(value, bool):
            if key == "web":
                continue
            if value:
                checkboxes.append(render_checkbox(key, checked=True))
                continue
            else:
                checkboxes.append(render_checkbox(key))
                continue
        elif isinstance(value, list):
            if key == 'appliances' or key == 'credentials':
                continue
            elif key in OBJECT_STATUS_ARGS:
                selects.append(render_multiselect_object_status(key, env))
                continue
            elif key == "StatusProvider":
                selects.append(render_multiselect_status_provider(key))
                continue
            elif key == "ObjectClass":
                selects.append(render_multiselect_object_class(key))
                continue
            textboxes.append(render_multitext(key))
        elif isinstance(value, str):
            if key == 'out_dir':
                continue
            elif key == 'out_file':
                continue
            elif key in OBJECT_STATUS_ARGS:
                selects.append(render_select_object_status(key, env))
                continue
            elif key == "StatusProvider":
                selects.append(render_select_status_provider(key))
                continue
            elif key == "ObjectClass":
                selects.append(render_select_object_class(key))
                continue
            elif "password" in key:
                textboxes.append(render_password_box(key, value))
                continue
            textboxes.append(render_textbox(key, value))
        elif isinstance(value, int):
            textboxes.append(render_textbox(key, value))
        elif value is None:
            if key == 'out_file':
                continue
            elif key == 'file_in':
                file_uploads.append(render_file_upload(plugin, key))
                continue
            textboxes.append(render_textbox(key, ''))

    forms.extend(textboxes)
    forms.extend(selects)
    forms.extend(file_uploads)
    forms.extend(checkboxes)
    forms.append(flask.render_template('submitbutton.html', plugin=plugin))
    forms.append('</div></div>')
    return '<br />\n'.join(forms)


def _call_method(func, kwargs):
    """Call func with kwargs if web is in kwargs, func should return a
    two-tupple containing (html, request_history). Here, we write the hsitory
    to a file and return the html for inclusion in the web GUI."""
    import random
    random.seed()
    if "appliances" not in kwargs:
        pass
    elif not kwargs["appliances"][0]:
        # Kind of a hack to return the response we want in case no appliances
        # were checked in the gui
        def _func(*args, **kwargs):
            return (
                "Must select at least one appliance.",
                "Must select at least one appliance.")
        func = _func
    if "web" in kwargs:
        try:
            # A legacy command can call a command that reports its
            # progress with mast.cli.say, which is for the terminal
            with captured():
                out, hist = func(**kwargs)
        except Exception as e:
            # The actions implemented should handle their own exceptions,
            # but if one makes it's way up here, we need to let the user know
            # part of that is suppressing the exception (because otherwise
            # we have no way of sending back the details)
            import traceback
            msg = "Sorry, an unhandled exception occurred while "
            msg += "performing action:\n\n\t {}".format(str(e))
            out, hist = msg, traceback.format_exc()
            sys.stderr.write(traceback.format_exc())
        return Markup(out), _save_history(hist)


def _save_history(hist):
    """Write the request history to a file for the history download and
    return the id of the file."""
    import random
    random.seed()
    t = Timestamp()

    filename = os.path.join(
        "var", "www", "static", "tmp", "request_history", t.timestamp)
    if not os.path.exists(filename):
        os.makedirs(filename)
    rand = random.randint(10000, 99999)
    _id = "{}-{}.log".format(str(t.timestamp), str(rand))
    filename = os.path.join(filename, _id)
    with open(filename, 'wb') as fout:
        fout.write(hist.encode())
    return _id


def _model_values(command, form, t):
    """Gather the values for a command that declares its parameters
    with a model. Return a dict to build the model from. A value that
    the user left empty is left out, so that the model uses its
    default."""
    name = command.key
    values = {}
    for spec in cli_params.options(command.model):
        key, widget = spec.name, spec.widget
        if widget == widgets.CREDENTIALS:
            values[key] = [
                xordecode(
                    _.encode(), key=xorencode(
                        flask.request.cookies["9x4h/mmek/j.ahba.ckhafn"], key="_"))
                for _ in form.getlist(key + '[]')]
        elif widget == widgets.OUT_DIR:
            values[key] = os.path.join('tmp', 'web', name, t.timestamp)
        elif widget == widgets.OUT_FILE:
            extension = os.path.splitext(str(spec.default or ""))[1]
            values[key] = "/".join((
                "tmp", "web", name,
                "{}-{}{}".format(t.timestamp, name, extension)))
        elif spec.kind == "flag":
            values[key] = form.get(key) == 'true'
        elif spec.kind == "list":
            values[key] = form.getlist(key + '[]')
        elif form.get(key) not in (None, ""):
            values[key] = form.get(key)
    return values


def _call_command(command, values):
    """Call a command that declares its parameters with a model. Return
    the html of its result and the id of the saved request history."""
    appliances = [
        spec.name for spec in cli_params.options(command.model)
        if spec.widget == widgets.APPLIANCES]
    if appliances and not any(values.get(appliances[0], [])):
        out = hist = "Must select at least one appliance."
        return Markup(out), _save_history(hist)
    try:
        params = command.model(**values)
    except ValidationError as exc:
        out = hist = "; ".join(cli_params.error_messages(
            command.model, exc, flags=False))
        return Markup(html_escape(out)), _save_history(hist)
    try:
        # The progress messages are for the terminal
        with captured():
            result = command.fn(params)
        if isinstance(result, Result):
            out, hist = result.html(), result.history
        else:
            out, hist = "", ""
    except Exception as e:
        # See the comment in _call_method
        import traceback
        msg = "Sorry, an unhandled exception occurred while "
        msg += "performing action:\n\n\t {}".format(str(e))
        out, hist = html_escape(msg), traceback.format_exc()
        sys.stderr.write(traceback.format_exc())
    return Markup(out), _save_history(hist)


def call_method(plugin, form):
    """Gather the arguments and function name from form then invoke
    _call_method. Wrap the results in html and return them."""
    t = Timestamp()
    name = form.get("callable")
    command = _get_command(plugin, name)
    if command.model is not None:
        kwargs = _model_values(command, form, t)
        out, history_id = _call_command(command, kwargs)
        return _render_output(name, t, kwargs, out, history_id)
    arguments, func = _get_arguments(plugin, name)
    kwargs = {}
    for arg, default in arguments:
        if isinstance(default, bool):
            if arg == "web":
                kwargs[arg] = True
                continue
            value = form.get(arg)
            if value == 'true':
                kwargs[arg] = True
            else:
                kwargs[arg] = False
        elif isinstance(default, list):
            if arg == 'appliances':
                kwargs[arg] = form.getlist(arg + '[]')
            elif arg == 'credentials':
                kwargs[arg] = [
                    xordecode(
                        _.encode(), key=xorencode(
                            flask.request.cookies["9x4h/mmek/j.ahba.ckhafn"], key="_"))
                            for _ in form.getlist(arg + '[]')]
            else:
                kwargs[arg] = form.getlist(arg + '[]')
        elif isinstance(default, str):
            if arg == 'out_dir':
                kwargs[arg] = os.path.join('tmp', 'web', name, t.timestamp)
            elif arg == 'out_file' and default is not None:
                kwargs[arg] = os.path.join("tmp",
                                           "web",
                                           name,
                                           "{}-{}{}".format(t.timestamp,
                                                            name,
                                                            os.path.splitext(default)[1])
                ).replace(os.path.sep, "/")
            else:
                kwargs[arg] = form.get(arg) or default
        elif isinstance(default, int):
            kwargs[arg] = int(form.get(arg)) or default
        elif default is None:
            kwargs[arg] = form.get(arg) or default
    out, history_id = _call_method(func, kwargs)
    return _render_output(name, t, kwargs, out, history_id)


def _render_output(name, t, kwargs, out, history_id):
    """Wrap the output of a command in html, with a link to download
    what the command wrote to out_dir or out_file."""
    link = ""
    if 'out_dir' in kwargs:
        config = get_config("server.conf")
        static_dir = config.get('dirs', 'static')

        fname = ""
        for appliance in kwargs['appliances']:
            fname = "{}-{}".format(fname, appliance)
        fname = "{}-{}{}.zip".format(t.timestamp, name, fname)
        zip_filename = os.path.join(
            static_dir,
            'tmp',
            fname)
        zip_file = zipfile.ZipFile(zip_filename, 'w', zipfile.ZIP_DEFLATED)
        _zipdir(kwargs['out_dir'], zip_file)
        zip_file.close()
        #filename = '%s-%s.zip' % (t.timestamp, name)
        link = Markup(flask.render_template('link.html', filename=fname))
    if 'out_file' in kwargs and kwargs["out_file"] is not None:
        import shutil
        config = get_config("server.conf")
        static_dir = config.get('dirs', 'static')
        dst = os.path.join(static_dir,
                           "tmp",
                           os.path.basename(kwargs["out_file"]))
        shutil.copyfile(kwargs["out_file"], dst)

        link = Markup(flask.render_template('link.html',
                                                  filename=os.path.basename(kwargs["out_file"])))
    out = flask.render_template(
        'output.html',
        output=out,
        callable=name,
        timestamp=str(t),
        history_id=history_id,
        link=link)
    if 'out_dir' in kwargs:
        out = out
    return out


def handle(plugin):
    """main funcion which will be routed to the plugin's endpoint"""
    logger = make_logger("mast.plugin_functions")
    import urllib.request, urllib.parse, urllib.error
    if flask.request.method == 'GET':
        logger.info("GET Request received")
        name = flask.request.args.get('callable')
        logger.debug("name: {}".format(name))
        appliances = flask.request.args.getlist('appliances[]')
        logger.debug("appliances: {}".format(str(appliances)))
        credentials = [xordecode(urllib.parse.unquote(_).encode(), key=xorencode(
                        flask.request.cookies["9x4h/mmek/j.ahba.ckhafn"], key="_"))
                        for _ in flask.request.args.getlist('credentials[]')]
        logger.debug("getting form")
        try:
            form = get_form(plugin.replace("mast.", ""), name, appliances, credentials)
        except Exception:
            logger.exception("An unhandled exception occurred during execution.")
            raise
        logger.debug("Got form")
        return form
    elif flask.request.method == 'POST':
        logger.info("Received POST request for {}".format(plugin))
        try:
            return Markup(str(call_method(plugin, flask.request.form)))
        except Exception:
            logger.exception("An unhandled exception occurred during processing of request.")
            raise

"""Current Telegram Postbox schema identifiers used by the exporter."""

from __future__ import annotations

import enum


class MessageNamespace(enum.IntEnum):
    """Current Telegram message-id namespaces."""

    CLOUD = 0
    LOCAL = 1
    SECRET_INCOMING = 2
    SCHEDULED_CLOUD = 3
    SCHEDULED_LOCAL = 4
    QUICK_REPLY_CLOUD = 5
    QUICK_REPLY_LOCAL = 6


class MediaNamespace(enum.IntEnum):
    """Current Telegram media-id namespaces."""

    CLOUD_IMAGE = 0
    CLOUD_AUDIO = 2
    CLOUD_CONTACT = 3
    CLOUD_MAP = 4
    CLOUD_FILE = 5
    CLOUD_WEBPAGE = 6
    LOCAL_IMAGE = 7
    LOCAL_FILE = 8
    CLOUD_SECRET_IMAGE = 9
    CLOUD_SECRET_FILE = 10
    CLOUD_GAME = 11
    CLOUD_INVOICE = 12
    LOCAL_WEBPAGE = 13
    LOCAL_POLL = 14
    CLOUD_POLL = 15


class PeerNamespace(enum.IntEnum):
    """Current Telegram peer-id namespaces."""

    CLOUD_USER = 0
    CLOUD_GROUP = 1
    CLOUD_CHANNEL = 2
    SECRET_CHAT = 3


class PhoneCallDiscardReason(enum.IntEnum):
    """Discard reasons stored by phone-call service actions."""

    MISSED = 0
    DISCONNECT = 1
    HANGUP = 2
    BUSY = 3


class SentSecureValueType(enum.IntEnum):
    """Secure-value kinds stored by bot service actions."""

    PERSONAL_DETAILS = 0
    PASSPORT = 1
    DRIVERS_LICENSE = 2
    ID_CARD = 3
    ADDRESS = 4
    BANK_STATEMENT = 5
    UTILITY_BILL = 6
    RENTAL_AGREEMENT = 7
    PHONE = 8
    EMAIL = 9
    INTERNAL_PASSPORT = 10
    PASSPORT_REGISTRATION = 11
    TEMPORARY_REGISTRATION = 12


class BotSendMessageAccessGrantedType(enum.IntEnum):
    """Bot access grant kinds."""

    ATTACH_MENU = 0
    REQUEST = 1


class ForumTopicEditComponentType(enum.IntEnum):
    """Discriminators for forum-topic edit components."""

    TITLE = 0
    ICON_FILE_ID = 1
    IS_CLOSED = 2
    IS_HIDDEN = 3


class ConferenceCallFlags(enum.IntFlag):
    """Flags stored by conference-call actions."""

    IS_VIDEO = 1 << 0
    IS_ACTIVE = 1 << 1
    IS_MISSED = 1 << 2


class StarGiftAuctionBidFlags(enum.IntFlag):
    """Flags stored by star-gift auction actions."""

    IS_ACQUIRED = 1 << 0
    IS_OUTBID = 1 << 1
    IS_RETURNED = 1 << 2
    IS_FINAL = 1 << 3


class SuggestedPostApprovalStatusType(enum.IntEnum):
    """Discriminators for suggested-post approval status."""

    APPROVED = 0
    REJECTED = 1


class SuggestedPostRejectionReason(enum.IntEnum):
    """Suggested-post rejection reason discriminators."""

    GENERIC = 0
    LOW_BALANCE = 1


class GroupCreatorChangeKind(enum.IntEnum):
    """Group creator change state."""

    PENDING = 0
    APPLIED = 1


class PostboxTable(enum.IntEnum):
    """Postbox SQLite table identifiers from Telegram-iOS."""

    METADATA = 0
    KEYCHAIN = 1
    PEER = 2
    GLOBAL_MESSAGE_IDS = 3
    MESSAGE_HISTORY_INDEX = 4
    MESSAGE_MEDIA = 6
    MESSAGE_HISTORY = 7
    CHAT_LIST_INDEX = 8
    CHAT_LIST = 9
    MESSAGE_HISTORY_METADATA = 10
    MESSAGE_HISTORY_UNSENT = 11
    MESSAGE_HISTORY_TAGS = 12
    PEER_CHAT_STATE = 13
    MESSAGE_HISTORY_READ_STATE = 14
    MESSAGE_HISTORY_SYNCHRONIZE_READ_STATE = 15
    CONTACT = 16
    PEER_RATING = 17
    CACHED_PEER_DATA = 18
    PEER_NOTIFICATION_SETTINGS = 19
    PEER_PRESENCE = 20
    ITEM_COLLECTION_INFO = 21
    ITEM_COLLECTION_ITEM = 22
    ITEM_CACHE_META = 24
    ITEM_CACHE = 25
    PEER_NAME_TOKEN_INDEX = 26
    PEER_NAME_INDEX = 27
    PEER_CHAT_TOP_TAGGED_MESSAGE_IDS = 28
    PEER_OPERATION_LOG_METADATA = 29
    PEER_MERGED_OPERATION_LOG_INDEX = 30
    PEER_OPERATION_LOG = 31
    MESSAGE_GLOBALLY_UNIQUE_ID = 32
    TIMESTAMP_MESSAGE_ATTRIBUTES_INDEX = 33
    TIMESTAMP_MESSAGE_ATTRIBUTES = 34
    PREFERENCES = 35
    ITEM_COLLECTION_REVERSE_INDEX = 36
    ORDERED_ITEM_LIST_INDEX = 37
    ORDERED_ITEM_LIST = 38
    GLOBAL_MESSAGE_HISTORY_TAGS = 39
    REVERSE_ASSOCIATED_PEER = 40
    MESSAGE_HISTORY_TEXT_INDEX = 41
    UNORDERED_ITEM_LIST = 42
    NOTICE = 43
    MESSAGE_HISTORY_TAGS_SUMMARY = 44
    PENDING_MESSAGE_ACTIONS_METADATA = 45
    PENDING_MESSAGE_ACTIONS = 46
    INVALIDATED_MESSAGE_HISTORY_TAGS_SUMMARY = 47
    PENDING_PEER_NOTIFICATION_SETTINGS_INDEX = 48
    MESSAGE_HISTORY_FAILED = 49
    LOCAL_MESSAGE_HISTORY_TAGS = 52
    DEVICE_CONTACT_IMPORT_INFO = 54
    ADDITIONAL_CHAT_LIST_ITEMS = 55
    MESSAGE_HISTORY_HOLE_INDEX = 56
    GROUP_MESSAGE_STATS = 58
    INVALIDATED_GROUP_MESSAGE_STATS = 59
    PEER_NOTIFICATION_SETTINGS_BEHAVIOR_INDEX = 60
    PEER_NOTIFICATION_SETTINGS_BEHAVIOR = 61
    MESSAGE_HISTORY_THREADS = 62
    MESSAGE_HISTORY_THREAD_HOLE_INDEX = 63
    PEER_TIMEOUT_PROPERTIES = 64
    STORY_GENERAL_STATES = 65
    PEER_CHAT_INTERFACE_STATE = 67
    PEER_CHAT_THREAD_INTERFACE_STATE = 68
    STORY_ITEMS = 69
    STORY = 70
    MESSAGE_HISTORY_THREAD_TAGS = 71
    MESSAGE_HISTORY_THREAD_REVERSE_INDEX = 72
    MESSAGE_HISTORY_THREAD_INDEX = 73
    PEER_THREADS_SUMMARY = 75
    MESSAGE_HISTORY_THREAD_PINNED = 76
    PEER_THREAD_COMBINED_STATE = 77
    STORY_SUBSCRIPTIONS = 78
    STORY_PEER_STATES = 79
    STORY_TOP_ITEMS = 80
    MESSAGE_CUSTOM_TAG_ID = 81
    MESSAGE_CUSTOM_TAG_HOLE_INDEX = 82
    MESSAGE_CUSTOM_TAG = 83
    MESSAGE_CUSTOM_TAG_WITH_TAG_HOLE_INDEX = 84
    MESSAGE_CUSTOM_TAG_WITH_TAG = 85

    @property
    def sqlite_name(self) -> str:
        """Return the physical SQLite table name."""
        return f"t{int(self)}"


class TelegramMediaActionType(enum.IntEnum):
    """TelegramMediaActionType discriminators from Telegram-iOS."""

    UNKNOWN = 0
    GROUP_CREATED = 1
    ADDED_MEMBERS = 2
    REMOVED_MEMBERS = 3
    PHOTO_UPDATED = 4
    TITLE_UPDATED = 5
    PINNED_MESSAGE_UPDATED = 6
    JOINED_BY_LINK = 7
    CHANNEL_MIGRATED_FROM_GROUP = 8
    GROUP_MIGRATED_TO_CHANNEL = 9
    HISTORY_CLEARED = 10
    HISTORY_SCREENSHOT = 11
    MESSAGE_AUTOREMOVE_TIMEOUT_UPDATED = 12
    GAME_SCORE = 13
    PHONE_CALL = 14
    PAYMENT_SENT = 15
    CUSTOM_TEXT = 16
    BOT_DOMAIN_ACCESS_GRANTED = 17
    BOT_SENT_SECURE_VALUES = 18
    PEER_JOINED = 19
    PHONE_NUMBER_REQUEST = 20
    GEO_PROXIMITY_REACHED = 21
    GROUP_PHONE_CALL = 22
    INVITE_TO_GROUP_PHONE_CALL = 23
    SET_CHAT_THEME = 24
    JOINED_BY_REQUEST = 25
    WEB_VIEW_DATA = 26
    GIFT_PREMIUM = 27
    TOPIC_CREATED = 28
    TOPIC_EDITED = 29
    SUGGESTED_PROFILE_PHOTO = 30
    ATTACH_MENU_BOT_ALLOWED = 31
    REQUESTED_PEER = 32
    SET_CHAT_WALLPAPER = 33
    SET_SAME_CHAT_WALLPAPER = 34
    BOT_APP_ACCESS_GRANTED = 35
    GIFT_CODE = 36
    GIVEAWAY_LAUNCHED = 37
    JOINED_CHANNEL = 38
    GIVEAWAY_RESULTS = 39
    BOOSTS_APPLIED = 40
    PAYMENT_REFUNDED = 41
    GIFT_STARS = 42
    PRIZE_STARS = 43
    STAR_GIFT = 44
    STAR_GIFT_UNIQUE = 45
    PAID_MESSAGES_REFUNDED = 46
    PAID_MESSAGES_PRICE_EDITED = 47
    CONFERENCE_CALL = 48
    TODO_COMPLETIONS = 49
    TODO_APPEND_TASKS = 50
    SUGGESTED_POST_APPROVAL_STATUS = 51
    GIFT_TON = 52
    SUGGESTED_POST_SUCCESS = 53
    SUGGESTED_POST_REFUND = 54
    SUGGESTED_BIRTHDAY = 55
    STAR_GIFT_PURCHASE_OFFER = 56
    STAR_GIFT_PURCHASE_OFFER_DECLINED = 57
    GROUP_CREATOR_CHANGE = 59
    COPY_PROTECTION_TOGGLE = 60
    COPY_PROTECTION_REQUEST = 61
    MANAGED_BOT_CREATED = 62
    POLL_OPTION_APPENDED = 63
    POLL_OPTION_DELETED = 64


# Postbox-coded Media implementations in the current TelegramCore SyncCore.
POSTBOX_MEDIA_TYPES = (
    "TelegramMediaAction",
    "TelegramMediaContact",
    "TelegramMediaDice",
    "TelegramMediaExpiredContent",
    "TelegramMediaFile",
    "TelegramMediaGame",
    "TelegramMediaGiveaway",
    "TelegramMediaGiveawayResults",
    "TelegramMediaImage",
    "TelegramMediaInvoice",
    "TelegramMediaLiveStream",
    "TelegramMediaMap",
    "TelegramMediaPaidContent",
    "TelegramMediaPoll",
    "TelegramMediaStory",
    "TelegramMediaTodo",
    "TelegramMediaUnsupported",
    "TelegramMediaWebpage",
)


# Nested media/resource objects commonly reached from message media payloads.
POSTBOX_MEDIA_HELPER_TYPES = (
    "CloudDocumentMediaResource",
    "CloudDocumentSizeMediaResource",
    "CloudFileMediaResource",
    "CloudPeerPhotoSizeMediaResource",
    "CloudPhotoSizeMediaResource",
    "CloudStickerPackThumbnailMediaResource",
    "Completion",
    "CurrencyAmount",
    "EmojiMarkup",
    "EmptyMediaResource",
    "ForumTopicEditComponent",
    "HttpReferenceMediaResource",
    "Item",
    "LocalFileMediaResource",
    "LocalFileReferenceMediaResource",
    "MapGeoAddress",
    "MapVenue",
    "SecretFileMediaResource",
    "SecureFileMediaResource",
    "StarGift",
    "StickerPackReference",
    "SuggestedPostApprovalStatus",
    "TelegraMediaWebpageThemeAttribute",
    "TelegramBirthday",
    "TelegramMediaFileAttribute",
    "TelegramMediaFileReference",
    "TelegramMediaImageReference",
    "TelegramMediaImageRepresentation",
    "TelegramMediaPollOption",
    "TelegramMediaPollOptionVoters",
    "TelegramMediaPollResults",
    "TelegramMediaWebpageAITextStyleAttribute",
    "TelegramMediaWebpageGiftAuctionAttribute",
    "TelegramMediaWebpageGiftCollectionAttribute",
    "TelegramMediaWebpageLoadedContent",
    "TelegramMediaWebpageStarGiftAttribute",
    "TelegramMediaWebpageStickerPackAttribute",
    "TelegramWallpaper",
    "VideoRepresentation",
    "VideoThumbnail",
    "WebFileReferenceMediaResource",
    "WallpaperDataResource",
)


POSTBOX_MESSAGE_ATTRIBUTE_TYPES = (
    "AudioTranscriptionMessageAttribute",
    "AuthorSignatureMessageAttribute",
    "AuthSessionInfoAttribute",
    "AutoclearTimeoutMessageAttribute",
    "AutoremoveTimeoutMessageAttribute",
    "BoostCountMessageAttribute",
    "ChannelMessageStateVersionAttribute",
    "ConsumableContentMessageAttribute",
    "ConsumablePersonalMentionMessageAttribute",
    "ContentRequiresValidationMessageAttribute",
    "DerivedDataMessageAttribute",
    "EditedMessageAttribute",
    "EffectMessageAttribute",
    "EmbeddedMediaStickersMessageAttribute",
    "EmojiSearchQueryMessageAttribute",
    "FactCheckMessageAttribute",
    "ForwardCountMessageAttribute",
    "ForwardOptionsMessageAttribute",
    "ForwardSourceInfoAttribute",
    "ForwardVideoTimestampAttribute",
    "GuestChatMessageAttribute",
    "InlineBotMessageAttribute",
    "InlineBusinessBotMessageAttribute",
    "LocalMediaPlaybackInfoAttribute",
    "MediaSpoilerMessageAttribute",
    "MessageReaction",
    "MessageTextEntityType",
    "NonPremiumMessageAttribute",
    "NotificationInfoMessageAttributeFlags",
    "OutgoingChatContextResultMessageAttribute",
    "OutgoingContentInfoFlags",
    "OutgoingMessageInfoFlags",
    "OutgoingQuickReplyMessageAttribute",
    "OutgoingScheduleInfoMessageAttribute",
    "PaidStarsMessageAttribute",
    "ParticipantRankMessageAttribute",
    "PeerGroupMessageStateVersionAttribute",
    "PendingProcessingMessageAttribute",
    "PendingReactionsMessageAttribute",
    "PendingStarsReactionsMessageAttribute",
    "PublishedSuggestedPostMessageAttribute",
    "QuotedReplyMessageAttribute",
    "ReactionsMessageAttribute",
    "ReplyMarkupButton",
    "ReplyMarkupButtonRequestPeerType",
    "ReplyMarkupRow",
    "ReplyMessageAttribute",
    "ReplyStoryAttribute",
    "ReplyThreadMessageAttribute",
    "ReportDeliveryMessageAttribute",
    "RestrictedContentMessageAttribute",
    "ScheduledRepeatAttribute",
    "SendAsMessageAttribute",
    "SourceAuthorInfoMessageAttribute",
    "SourceReferenceMessageAttribute",
    "SuggestedPostMessageAttribute",
    "SummarizationMessageAttribute",
    "TextEntitiesMessageAttribute",
    "TopPeer",
    "TranslationMessageAttribute",
    "ValidationMessageAttribute",
    "ViewCountMessageAttribute",
)


POSTBOX_FIELD_ALIASES: dict[str, dict[str, str]] = {
    "CloudFileMediaResource": {
        "d": "datacenter_id",
        "v": "volume_id",
        "l": "local_id",
        "s": "secret",
        "n64": "size",
        "n": "legacy_size",
        "fr": "file_reference",
    },
    "CloudDocumentMediaResource": {
        "d": "datacenter_id",
        "f": "file_id",
        "a": "access_hash",
        "n64": "size",
        "n": "legacy_size",
        "fr": "file_reference",
        "fn": "file_name",
    },
    "CloudDocumentSizeMediaResource": {
        "d": "datacenter_id",
        "i": "document_id",
        "h": "access_hash",
        "s": "size_spec",
        "fr": "file_reference",
    },
    "CloudPeerPhotoSizeMediaResource": {
        "d": "datacenter_id",
        "p": "photo_id",
        "s": "size_spec",
        "v": "volume_id",
        "l": "local_id",
    },
    "CloudPhotoSizeMediaResource": {
        "d": "datacenter_id",
        "i": "photo_id",
        "h": "access_hash",
        "s": "size_spec",
        "n64": "size",
        "n": "legacy_size",
        "fr": "file_reference",
    },
    "CloudStickerPackThumbnailMediaResource": {
        "d": "datacenter_id",
        "t": "thumb_version",
        "v": "volume_id",
        "l": "local_id",
    },
    "HttpReferenceMediaResource": {
        "u": "url",
        "s64": "size",
        "s": "legacy_size",
    },
    "LocalFileMediaResource": {
        "f": "file_id",
        "sr": "is_secret_related",
        "s64": "size",
        "s": "legacy_size",
    },
    "LocalFileReferenceMediaResource": {
        "p": "local_file_path",
        "r": "random_id",
        "t": "is_uniquely_referenced_temporary_file",
        "s64": "size",
        "s": "legacy_size",
    },
    "SecretFileMediaResource": {
        "i": "file_id",
        "a": "access_hash",
        "s64": "container_size",
        "s": "legacy_container_size",
        "ds64": "decrypted_size",
        "ds": "legacy_decrypted_size",
        "d": "datacenter_id",
        "k": "key",
    },
    "SecureFileMediaResource": {
        "f": "file_id",
        "a": "access_hash",
        "n64": "size",
        "n": "legacy_size",
        "d": "datacenter_id",
        "t": "timestamp",
        "h": "file_hash",
        "s": "encrypted_secret",
    },
    "TelegramMediaContact": {
        "n.f": "first_name",
        "n.l": "last_name",
        "pn": "phone_number",
        "p": "peer_id",
        "vc": "vcard",
    },
    "TelegramMediaDice": {
        "e": "emoji",
        "ta": "ton_amount",
        "v": "value",
        "gos": "game_outcome_seed",
        "goa": "game_outcome_amount",
    },
    "TelegramMediaExpiredContent": {"d": "data"},
    "TelegramMediaFile": {
        "i": "file_id",
        "prf": "partial_reference",
        "r": "resource",
        "pr": "preview_representations",
        "vr": "video_thumbnails",
        "cv": "video_cover",
        "itd": "immediate_thumbnail_data",
        "mt": "mime_type",
        "s64": "size",
        "s": "legacy_size",
        "at": "attributes",
        "arep": "alternative_representations",
    },
    "TelegramMediaGame": {
        "i": "game_id",
        "h": "access_hash",
        "n": "name",
        "t": "title",
        "d": "description",
        "p": "image",
        "f": "file",
    },
    "TelegramMediaImage": {
        "i": "image_id",
        "r": "representations",
        "vr": "video_representations",
        "itd": "immediate_thumbnail_data",
        "em": "emoji_markup",
        "rf": "reference",
        "prf": "partial_reference",
        "fl": "flags",
        "vid": "video",
    },
    "TelegramMediaImageRepresentation": {
        "dx": "width",
        "dy": "height",
        "r": "resource",
        "ps": "progressive_sizes",
        "th": "immediate_thumbnail_data",
        "hv": "has_video",
        "ip": "is_personal",
    },
    "VideoThumbnail": {
        "w": "width",
        "h": "height",
        "r": "resource",
    },
    "VideoRepresentation": {
        "w": "width",
        "h": "height",
        "r": "resource",
        "s": "start_timestamp",
    },
    "WallpaperDataResource": {"s": "slug"},
    "WebFileReferenceMediaResource": {
        "u": "url",
        "s64": "size",
        "s": "legacy_size",
        "h": "access_hash",
    },
    "TelegramMediaStory": {
        "pid": "peer_id",
        "sid": "story_id",
        "mns": "is_mention",
    },
    "TelegramMediaTodo": {
        "f": "flags",
        "t": "text",
        "te": "text_entities",
        "is": "items",
        "cs": "completions",
    },
    "TelegramMediaWebpage": {
        "i": "webpage_id",
        "ct": "content_type",
        "pendingDate": "pending_date",
        "pendingUrl": "pending_url",
        "u": "url",
        "d": "display_url",
        "ti": "title",
        "tx": "text",
    },
    "TelegramMediaWebpageLoadedContent": {
        "u": "url",
        "d": "display_url",
        "ha": "hash",
        "ty": "content_type",
        "ws": "website_name",
        "ti": "title",
        "tx": "text",
        "eu": "embed_url",
        "et": "embed_type",
        "esw": "embed_width",
        "esh": "embed_height",
        "du": "duration",
        "au": "author",
        "lbd": "is_media_large_by_default",
        "isvc": "image_is_video_cover",
        "im": "image",
        "fi": "file",
        "stry": "story",
        "attr": "attributes",
    },
}
